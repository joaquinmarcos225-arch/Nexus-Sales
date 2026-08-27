# Nexus Sales — Frontend

SPA React (Vite + Tailwind). La documentación principal del proyecto está en la [raíz del repo](../README.md).

## Comandos

```bash
npm install
cp .env.example .env
npm run dev      # desarrollo
npm run build    # producción → dist/
npm run preview  # preview del build local
```

## Variables

| Variable | Descripción |
|----------|-------------|
| `VITE_API_URL` | URL del backend FastAPI (debe coincidir con el puerto de uvicorn) |

En dev, Vite hace proxy al API para evitar CORS. Ver comentarios en `.env.example`.

## Producción

Build con `VITE_API_URL` apuntando al API público. Ver [docs/PRODUCTION.md](../docs/PRODUCTION.md).
