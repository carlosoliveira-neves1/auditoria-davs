# Conciliação de Vendas — Vercel (Static + Python API)

- Frontend: index.html (upload, dashboard, download do Excel)
- API Python (Flask): pi/compare.py
- Builder Vercel: @vercel/python (3.11) via ercel.json

## Deploy
1. Suba estes arquivos na raiz do repositório GitHub.
2. Na Vercel: New Project → Import Git Repository → Deploy.
   - Framework: Other
   - Build Command: (em branco)
   - Output Directory: (em branco)

## Endpoint
POST /api/compare (multipart/form-data)
- campos: movimento (arquivo), minhas (arquivo), 	ol (float opcional, ex.: 0.02)
