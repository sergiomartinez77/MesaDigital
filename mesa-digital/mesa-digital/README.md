# Mesa Digital

## Correr en local (sin Google, modo desarrollo)
    pip install -r requirements.txt
    DEV_MODE=1 uvicorn main:app --reload
Abre http://localhost:8000 y usa "Entrar en modo desarrollo".

## Activar el login con Google
1. https://console.cloud.google.com → crea un proyecto.
2. APIs y servicios → Pantalla de consentimiento OAuth (Externo) → completa nombre y correo.
3. Credenciales → Crear credenciales → ID de cliente de OAuth → Aplicación web.
4. En "Orígenes autorizados de JavaScript" agrega http://localhost:8000 y tu dominio de producción (https://...).
5. Copia el ID de cliente y arranca:
       GOOGLE_CLIENT_ID=xxxx.apps.googleusercontent.com SECRET_KEY=una-clave-larga uvicorn main:app

## Producción (Railway / Render)
Variables: GOOGLE_CLIENT_ID, SECRET_KEY (aleatoria y larga), HTTPS_ONLY=1,
DATABASE_URL (PostgreSQL: agrega `psycopg2-binary` a requirements.txt). NO actives DEV_MODE.
Comando: uvicorn main:app --host 0.0.0.0 --port $PORT
