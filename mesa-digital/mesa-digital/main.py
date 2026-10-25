"""Mesa Digital v2: panel del dueño + login con Google + menú público con pedidos.
Correr:  pip install -r requirements.txt && uvicorn main:app --reload
Variables de entorno: ver README.md
"""
import json, os, re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field as PField
from sqlmodel import Field, Session, SQLModel, create_engine, select
from starlette.middleware.sessions import SessionMiddleware

BASE = Path(__file__).parent / "static"
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
DEV_MODE = os.getenv("DEV_MODE") == "1"
_db = os.getenv("DATABASE_URL", "sqlite:///app.db")
if _db.startswith("postgres://"):  # Render/Railway entregan este formato
    _db = _db.replace("postgres://", "postgresql://", 1)
engine = create_engine(_db, pool_pre_ping=True)

SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
STATUSES = ["nuevo", "preparando", "listo", "entregado", "cancelado"]
CO = timedelta(hours=-5)  # Colombia


# ---------------- Modelos ----------------
class Owner(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    google_sub: str = Field(unique=True, index=True)
    email: str
    name: str = ""
    picture: str = ""


class Restaurant(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    owner_id: int = Field(foreign_key="owner.id", unique=True, index=True)
    name: str
    slug: str = Field(unique=True, index=True)
    whatsapp: str  # con indicativo: 573001234567
    is_open: bool = True


class Product(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    restaurant_id: int = Field(foreign_key="restaurant.id", index=True)
    name: str
    price: int
    stock: int = 0
    category: str = "General"
    description: str = ""
    available: bool = True


class Order(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    restaurant_id: int = Field(foreign_key="restaurant.id", index=True)
    customer_name: str
    customer_phone: str
    notes: str = ""
    items: str  # JSON [{product_id,name,qty,price}]
    total: int
    status: str = "nuevo"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------- Esquemas ----------------
class GoogleIn(BaseModel):
    credential: str


class RestaurantIn(BaseModel):
    name: str = PField(min_length=2, max_length=80)
    slug: str = PField(min_length=3, max_length=40)
    whatsapp: str = PField(min_length=10, max_length=15, pattern=r"^\d+$")
    is_open: bool = True


class ProductIn(BaseModel):
    name: str = PField(min_length=1, max_length=80)
    price: int = PField(ge=0)
    stock: int = PField(ge=0)
    category: str = PField(default="General", max_length=40)
    description: str = PField(default="", max_length=200)
    available: bool = True


class StatusIn(BaseModel):
    status: str


class LineIn(BaseModel):
    product_id: int
    qty: int = PField(ge=1, le=50)


class OrderIn(BaseModel):
    customer_name: str = PField(min_length=2, max_length=60)
    customer_phone: str = PField(min_length=7, max_length=20)
    notes: str = PField(default="", max_length=300)
    lines: list[LineIn] = PField(min_length=1)


# ---------------- App ----------------
app = FastAPI(title="Mesa Digital")
app.add_middleware(SessionMiddleware,
                   secret_key=os.getenv("SECRET_KEY", "dev-cambia-esto"),
                   max_age=60 * 60 * 24 * 30, same_site="lax",
                   https_only=os.getenv("HTTPS_ONLY") == "1")
SQLModel.metadata.create_all(engine)


def aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def page(name: str, **repl) -> HTMLResponse:
    html = (BASE / name).read_text(encoding="utf-8")
    for k, v in repl.items():
        html = html.replace(f"__{k}__", v)
    return HTMLResponse(html)


def owner_of(request: Request, s: Session) -> Owner:
    oid = request.session.get("owner_id")
    o = s.get(Owner, oid) if oid else None
    if not o:
        raise HTTPException(401, "No autenticado")
    return o


def rest_of(s: Session, o: Owner) -> Restaurant:
    r = s.exec(select(Restaurant).where(Restaurant.owner_id == o.id)).first()
    if not r:
        raise HTTPException(404, "Primero crea tu restaurante en Ajustes")
    return r


def order_out(o: Order) -> dict:
    d = o.model_dump()
    d["items"] = json.loads(o.items)
    d["created_at"] = aware(o.created_at).isoformat()
    return d


# ---------------- Autenticación ----------------
def _login(request: Request, sub: str, email: str, name: str, picture: str):
    with Session(engine) as s:
        o = s.exec(select(Owner).where(Owner.google_sub == sub)).first()
        if not o:
            o = Owner(google_sub=sub, email=email)
        o.email, o.name, o.picture = email, name, picture
        s.add(o); s.commit(); s.refresh(o)
        request.session["owner_id"] = o.id
    return {"ok": True}


@app.post("/auth/google")
def auth_google(data: GoogleIn, request: Request):
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(500, "Falta GOOGLE_CLIENT_ID en el servidor")
    from google.auth.transport import requests as greq
    from google.oauth2 import id_token
    try:
        info = id_token.verify_oauth2_token(data.credential, greq.Request(), GOOGLE_CLIENT_ID)
    except ValueError:
        raise HTTPException(401, "Token de Google inválido")
    except Exception:  # Google no responde / sin red
        raise HTTPException(503, "No se pudo verificar con Google, intenta de nuevo")
    if not info.get("email_verified"):
        raise HTTPException(401, "Correo de Google no verificado")
    return _login(request, info["sub"], info["email"], info.get("name", ""), info.get("picture", ""))


@app.get("/auth/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/")


if DEV_MODE:  # solo para pruebas locales, NUNCA en producción
    @app.get("/auth/dev")
    def dev_login(request: Request, email: str = "dev@example.com"):
        _login(request, "dev-" + email, email, email.split("@")[0], "")
        return RedirectResponse("/panel")


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    if request.session.get("owner_id"):
        return RedirectResponse("/panel")
    return page("login.html", CLIENT_ID=GOOGLE_CLIENT_ID, DEV="1" if DEV_MODE else "0")


@app.get("/panel", response_class=HTMLResponse)
def panel(request: Request):
    if not request.session.get("owner_id"):
        return RedirectResponse("/")
    return page("panel.html")


# ---------------- API del dueño ----------------
@app.get("/api/me")
def me(request: Request):
    with Session(engine) as s:
        o = owner_of(request, s)
        r = s.exec(select(Restaurant).where(Restaurant.owner_id == o.id)).first()
        return {"owner": {"name": o.name, "email": o.email, "picture": o.picture},
                "restaurant": r.model_dump() if r else None}


@app.put("/api/restaurant")
def save_restaurant(data: RestaurantIn, request: Request):
    if not SLUG_RE.match(data.slug):
        raise HTTPException(400, "El enlace solo puede tener minúsculas, números y guiones")
    with Session(engine) as s:
        o = owner_of(request, s)
        r = s.exec(select(Restaurant).where(Restaurant.owner_id == o.id)).first()
        taken = s.exec(select(Restaurant).where(Restaurant.slug == data.slug)).first()
        if taken and (not r or taken.id != r.id):
            raise HTTPException(400, "Ese enlace ya está en uso, prueba otro")
        if not r:
            r = Restaurant(owner_id=o.id, **data.model_dump())
        else:
            for k, v in data.model_dump().items():
                setattr(r, k, v)
        s.add(r); s.commit(); s.refresh(r)
        return r


@app.get("/api/products")
def list_products(request: Request):
    with Session(engine) as s:
        r = rest_of(s, owner_of(request, s))
        return s.exec(select(Product).where(Product.restaurant_id == r.id)
                      .order_by(Product.category, Product.name)).all()


@app.post("/api/products")
def create_product(data: ProductIn, request: Request):
    with Session(engine) as s:
        r = rest_of(s, owner_of(request, s))
        p = Product(restaurant_id=r.id, **data.model_dump())
        s.add(p); s.commit(); s.refresh(p)
        return p


@app.put("/api/products/{pid}")
def update_product(pid: int, data: ProductIn, request: Request):
    with Session(engine) as s:
        r = rest_of(s, owner_of(request, s))
        p = s.get(Product, pid)
        if not p or p.restaurant_id != r.id:
            raise HTTPException(404, "Producto no encontrado")
        for k, v in data.model_dump().items():
            setattr(p, k, v)
        s.add(p); s.commit(); s.refresh(p)
        return p


@app.delete("/api/products/{pid}")
def delete_product(pid: int, request: Request):
    with Session(engine) as s:
        r = rest_of(s, owner_of(request, s))
        p = s.get(Product, pid)
        if not p or p.restaurant_id != r.id:
            raise HTTPException(404, "Producto no encontrado")
        s.delete(p); s.commit()
        return {"ok": True}


@app.get("/api/orders")
def list_orders(request: Request):
    with Session(engine) as s:
        r = rest_of(s, owner_of(request, s))
        rows = s.exec(select(Order).where(Order.restaurant_id == r.id)
                      .order_by(Order.id.desc()).limit(200)).all()
        return [order_out(o) for o in rows]


@app.patch("/api/orders/{oid}")
def set_status(oid: int, data: StatusIn, request: Request):
    if data.status not in STATUSES:
        raise HTTPException(400, "Estado inválido")
    with Session(engine) as s:
        r = rest_of(s, owner_of(request, s))
        o = s.get(Order, oid)
        if not o or o.restaurant_id != r.id:
            raise HTTPException(404, "Pedido no encontrado")
        if o.status == "cancelado":
            raise HTTPException(400, "Un pedido cancelado no se puede reabrir")
        if data.status == "cancelado":  # devolver stock
            for it in json.loads(o.items):
                p = s.get(Product, it["product_id"])
                if p:
                    p.stock += it["qty"]; s.add(p)
        o.status = data.status
        s.add(o); s.commit(); s.refresh(o)
        return order_out(o)


@app.get("/api/stats")
def stats(request: Request):
    with Session(engine) as s:
        r = rest_of(s, owner_of(request, s))
        now = datetime.now(timezone.utc)
        today0 = (now + CO).replace(hour=0, minute=0, second=0, microsecond=0) - CO
        days = [today0 - timedelta(days=i) for i in range(6, -1, -1)]
        totals = {d: 0 for d in days}
        orders = s.exec(select(Order).where(Order.restaurant_id == r.id)
                        .order_by(Order.id.desc()).limit(1000)).all()
        sales_today = orders_today = pending = 0
        for o in orders:
            t = aware(o.created_at)
            if o.status in ("nuevo", "preparando", "listo"):
                pending += 1
            if o.status == "cancelado":
                continue
            for d in days:
                if d <= t < d + timedelta(days=1):
                    totals[d] += o.total
            if t >= today0:
                sales_today += o.total; orders_today += 1
        low = s.exec(select(Product).where(Product.restaurant_id == r.id,
                                           Product.available == True, Product.stock <= 3)  # noqa
                     .order_by(Product.stock)).all()
        return {"sales_today": sales_today, "orders_today": orders_today, "pending": pending,
                "last7": [{"label": (d + CO).strftime("%d/%m"),
                           "total": totals[d]} for d in days],
                "low_stock": [{"id": p.id, "name": p.name, "stock": p.stock} for p in low]}


# ---------------- Público (clientes) ----------------
def public_rest(s: Session, slug: str) -> Restaurant:
    r = s.exec(select(Restaurant).where(Restaurant.slug == slug)).first()
    if not r:
        raise HTTPException(404, "Restaurante no encontrado")
    return r


@app.get("/r/{slug}", response_class=HTMLResponse)
def public_page(slug: str):
    with Session(engine) as s:
        public_rest(s, slug)
    return page("menu.html")


@app.get("/r/{slug}/data")
def public_data(slug: str):
    with Session(engine) as s:
        r = public_rest(s, slug)
        prods = s.exec(select(Product).where(Product.restaurant_id == r.id,
                                             Product.available == True)  # noqa
                       .order_by(Product.category, Product.name)).all()
        return {"name": r.name, "is_open": r.is_open,
                "products": [{"id": p.id, "name": p.name, "price": p.price, "stock": p.stock,
                              "category": p.category, "description": p.description} for p in prods]}


@app.post("/r/{slug}/orders")
def place_order(slug: str, data: OrderIn):
    with Session(engine) as s:
        r = public_rest(s, slug)
        if not r.is_open:
            raise HTTPException(400, "El restaurante está cerrado por ahora")
        items, total = [], 0
        for ln in data.lines:
            p = s.get(Product, ln.product_id)
            if not p or p.restaurant_id != r.id or not p.available:
                raise HTTPException(400, "Producto no disponible")
            if p.stock < ln.qty:
                raise HTTPException(400, f"Solo quedan {p.stock} de {p.name}")
            p.stock -= ln.qty; s.add(p)
            items.append({"product_id": p.id, "name": p.name, "qty": ln.qty, "price": p.price})
            total += p.price * ln.qty
        o = Order(restaurant_id=r.id, customer_name=data.customer_name.strip(),
                  customer_phone=data.customer_phone.strip(), notes=data.notes.strip(),
                  items=json.dumps(items), total=total)
        s.add(o); s.commit(); s.refresh(o)
        det = "\n".join(f"{i['qty']}x {i['name']}" for i in items)
        msg = f"Hola, soy {o.customer_name}. Pedido #{o.id}:\n{det}\nTotal: ${total:,} COP"
        if o.notes:
            msg += f"\nNotas: {o.notes}"
        return {"order_id": o.id, "total": total,
                "whatsapp_url": f"https://wa.me/{r.whatsapp}?text={quote(msg)}"}
