# Inteligência de Mercado — Eventos

Coleta diária automática de posts de fornecedores de eventos no Instagram,
extração de dados estruturados com IA, e dashboard para visualizar quem está
fazendo o quê, quando e com quem na sua cidade.

---

## Instalação rápida

```bash
# 1. Criar ambiente virtual e instalar dependências
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Configurar variáveis de ambiente
cp .env.example .env
# edite .env com sua chave da Anthropic e sessão do Instagram

# 3. Inicializar banco de dados
python -c "import db; db.init_db()"
```

---

## Configurar fornecedores

Edite `vendors.json` com os fornecedores da sua cidade:

```json
[
  { "handle": "@nomedobuffet", "platform": "instagram", "category": "buffet", "name": "Nome do Buffet", "active": true },
  { "handle": "@seuConcorrente", "platform": "instagram", "category": "competitor", "name": "Locações Rival", "active": true }
]
```

Categorias disponíveis: `venue`, `planner`, `decorator`, `competitor`, `buffet`, `photographer`, `other`

---

## Configurar sessão do Instagram

Para maior confiabilidade, crie uma **conta dedicada** só para monitoramento (não use sua conta comercial).

1. Faça login nessa conta no Chrome
2. Abra DevTools → Application → Cookies → `https://www.instagram.com`
3. Copie o valor do cookie `sessionid`
4. Cole em `.env` como `IG_SESSION_ID=...`
5. Coloque o username dessa conta em `IG_USERNAME=...`

---

## Rodar manualmente

```bash
# Apenas scraping (busca posts novos)
python scraper.py

# Apenas extração (processa posts com Claude)
python extractor.py

# Ciclo completo: scrape → extract → cross-reference
python run_daily.py

# Scraping de um único fornecedor para teste
python scraper.py --handle @exemplo --limit 3
```

---

## Dashboard

```bash
python dashboard/server.py
# Abre em http://localhost:5050
```

---

## Automação diária (GitHub Actions)

1. Coloque este projeto num repositório GitHub
2. Vá em Settings → Secrets → Actions e adicione:
   - `ANTHROPIC_API_KEY`
   - `IG_SESSION_ID`
   - `IG_USERNAME`
3. O workflow em `.github/workflows/daily-scrape.yml` roda todo dia às 06h BRT

---

## Estrutura do banco de dados

| Tabela | Conteúdo |
|--------|----------|
| `vendors` | Cadastro de fornecedores monitorados |
| `posts` | Posts coletados (caption, data, handles marcados) |
| `events` | Eventos detectados (tipo, data, local) |
| `event_vendors` | Quais fornecedores participaram de cada evento |

---

## Custos estimados (Claude Haiku)

| Escala | Custo/mês estimado |
|--------|--------------------|
| 50 fornecedores × 10 posts/dia | ~R$ 3–8/mês |
| 200 fornecedores × 10 posts/dia | ~R$ 12–30/mês |
