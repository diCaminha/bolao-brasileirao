import yaml
import pandas as pd
import requests
import re
import urllib3
from bs4 import BeautifulSoup
from flask import Flask, render_template_string, abort, url_for
from typing import List, Dict

# ---------------------------------------------------
# Configurações gerais
# ---------------------------------------------------
# Possible sources for the current standings.  The first URL is the one used in
# the original version of the application.  If it fails (for instance because
# the page was moved) we try the second CNN link and finally a table from
# Globo's sports website.
CNN_URLS = [
    "https://www.cnnbrasil.com.br/esportes/futebol/tabela-do-brasileirao/",
    "https://www.cnnbrasil.com.br/esportes/futebol/tabela-do-brasileirao-serie-a/",
]
GE_URL = "https://ge.globo.com/futebol/brasileirao/"
PARTICIPANTS_FILE = "participantes.yml"
PORT = 5000
# Some websites refuse connections from uncommon user-agents.  Pretend to be a
# regular browser to increase our chances of success.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)

# ---------------------------------------------------
# Templates – estilo Material Design via Bootstrap 5 + Google Fonts
# ---------------------------------------------------
GOOGLE_FONTS = "https://fonts.googleapis.com/css2?family=Roboto:wght@300;400;500;700&display=swap"
CUSTOM_CSS = '''
<style>
  :root {
    --md-primary: #1976d2;
    --md-primary-dark: #004ba0;
    --md-accent: #ff9800;
  }
  body { font-family: 'Roboto', sans-serif; }
  .card { border: none; border-radius: 1rem; box-shadow: 0 4px 12px rgba(0,0,0,.1); }
  .card-header { background: var(--md-primary); color: #fff; font-weight: 500; border-radius: 1rem 1rem 0 0; }
  .btn-md { border-radius: 2rem; font-weight: 500; }
  .table thead th { background: var(--md-primary-dark); color: #fff; }
  .table-success { background: #c8e6c9 !important; }
</style>
'''

INDEX_TEMPLATE = '''
<!doctype html>
<html lang="pt-br">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Ranking de Palpites - Brasileirão 2025</title>
    <link rel="preconnect" href="https://fonts.gstatic.com">
    <link href="{{ google_fonts }}" rel="stylesheet">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    {{ custom_css|safe }}
  </head>
  <body class="bg-light">
    <div class="container py-5">
      <div class="d-flex justify-content-between align-items-center mb-4">
        <h1 class="fw-bold text-dark">Ranking de Palpites <small class="text-muted fs-5">2025</small></h1>
        <a class="btn btn-md btn-primary shadow-sm" href="{{ url_for('comparativo') }}">Comparativo Detalhado</a>
      </div>

      <div class="row g-4">
        <div class="col-lg-7">
          <div class="card h-100">
            <div class="card-header">Ranking de Participantes</div>
            <div class="card-body p-0">
              <table class="table mb-0 table-sm align-middle">
                <thead><tr><th>#</th><th>Participante</th><th>Erro Total</th></tr></thead>
                <tbody>
                  {% for participant, score in participants %}
                    <tr><td>{{ loop.index }}</td><td>{{ participant }}</td><td>{{ score }}</td></tr>
                  {% endfor %}
                </tbody>
              </table>
            </div>
          </div>
        </div>
        <div class="col-lg-5">
          <div class="card h-100">
            <div class="card-header">Classificação Atual</div>
            <div class="card-body p-0">
              <table class="table mb-0 table-sm">
                <thead><tr><th>#</th><th>Time</th></tr></thead>
                <tbody>
                  {% for team in standings %}
                    <tr><td>{{ loop.index }}</td><td>{{ team }}</td></tr>
                  {% endfor %}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </div>
  </body>
</html>
'''

COMPARATIVE_TEMPLATE = '''
<!doctype html>
<html lang="pt-br">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Comparativo de Palpites - Brasileirão 2025</title>
    <link rel="preconnect" href="https://fonts.gstatic.com">
    <link href="{{ google_fonts }}" rel="stylesheet">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    {{ custom_css|safe }}
  </head>
  <body class="bg-light">
    <div class="container py-5">
      <div class="d-flex justify-content-between align-items-center mb-4">
        <a class="btn btn-md btn-secondary shadow-sm" href="{{ url_for('index') }}">← Voltar</a>
        <h1 class="fw-bold text-dark flex-grow-1 text-center">Comparativo</h1>
        <div style="width:90px"></div>
      </div>
      {% for participant, data in comparativo.items() %}
        <div class="card mb-5">
          <div class="card-header d-flex justify-content-between align-items-center">
            <span class="fw-semibold">{{ participant }}</span>
            <span class="badge bg-primary rounded-pill">Erro: {{ data.total }}</span>
          </div>
          <div class="card-body p-0">
            <table class="table mb-0 table-sm align-middle">
              <thead><tr><th>Real</th><th>Time</th><th>Prevista</th><th>Δ</th></tr></thead>
              <tbody>
                {% for row in data.rows %}
                  <tr{% if row.diff == 0 %} class="table-success"{% endif %}>
                    <td>{{ row.real }}</td>
                    <td>{{ row.team }}</td>
                    <td>{{ '-' if row.pred is none else row.pred }}</td>
                    <td>{{ row.diff }}</td>
                  </tr>
                {% endfor %}
              </tbody>
            </table>
          </div>
        </div>
      {% endfor %}
    </div>
  </body>
</html>
'''
# ---------------------------------------------------
# Funções auxiliares
# ---------------------------------------------------

def load_predictions(path: str = PARTICIPANTS_FILE) -> Dict[str, List[str]]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _extract_team_from_cell(cell_text: str) -> str:
    parts = cell_text.split()
    if len(parts) < 2:
        return ""
    core = parts[1:-1] if len(parts) >= 3 else parts[1:]
    return " ".join(core)


def _parse_standings_html(html: str) -> List[str]:
    """Extracts the team names from an HTML page.

    The logic tries first with ``pandas.read_html`` and then falls back to
    manual parsing with BeautifulSoup.  Returns a list of team names or an empty
    list if nothing suitable is found.
    """
    # Tentativa 1: pandas
    try:
        dfs = pd.read_html(html)
    except ValueError:
        dfs = []
    for df in dfs:
        if len(df) < 18:
            continue
        first_col = df.columns[0]
        names = [_extract_team_from_cell(str(v)) for v in df[first_col]]
        names = [n for n in names if n]
        if len(names) >= 18:
            return names[:20]
    # Tentativa 2: BS4
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        return []
    teams = []
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        pos_text = cells[0].get_text(strip=True)
        if not re.fullmatch(r"\d+", pos_text):
            continue
        raw = cells[0].get_text(" ", strip=True) + " " + cells[1].get_text(" ", strip=True)
        name = _extract_team_from_cell(raw)
        if name:
            teams.append(name)
        if len(teams) >= 20:
            break
    return teams


def get_real_standings() -> List[str]:
    """Obtém a classificação atual do Brasileirão.

    Tenta primeiro as URLs da CNN Brasil.  Caso nenhuma funcione, utiliza a
    página do site GE Globo como fonte alternativa.  Lança um erro 500 se não
    for possível extrair ao menos 18 clubes de nenhuma das fontes.
    """
    urls = CNN_URLS + [GE_URL]
    last_error: Exception | None = None
    for url in urls:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15, verify=False)
            resp.raise_for_status()
        except Exception as exc:
            last_error = exc
            continue

        teams = _parse_standings_html(resp.text)
        if len(teams) >= 18:
            return teams[:20]
        last_error = ValueError("menos de 18 clubes extraidos")

    abort(500, f"Erro ao obter classificação: {last_error}")


def calculate_scores(predictions: Dict[str, List[str]], real: List[str]) -> Dict[str, int]:
    real_pos = {team: idx + 1 for idx, team in enumerate(real)}
    return {
        name: sum(abs(i - real_pos.get(team, len(real))) for i, team in enumerate(guess, start=1))
        for name, guess in predictions.items()
    }


def build_comparativo(predictions: Dict[str, List[str]], real: List[str]) -> Dict[str, Dict]:
    """Gera, para cada participante, uma linha por clube (1‑20).

    • Se o participante não previu o clube, posição prevista fica "-" e
      diferença considera o máximo (len(real)).
    """
    comparativo: Dict[str, Dict] = {}
    n = len(real)
    real_pos = {team: idx + 1 for idx, team in enumerate(real)}
    for participant, guess in predictions.items():
        rows: List[Dict[str, int | str | None]] = []
        total = 0
        for idx, team in enumerate(real, start=1):  # garante 20 linhas
            if team in guess:
                pred_pos = guess.index(team) + 1
                diff = abs(pred_pos - idx)
            else:
                pred_pos = None  # não previu
                diff = n  # penalidade máxima, mantemos coerente com calculate_scores
            total += diff
            rows.append({"team": team, "real": idx, "pred": pred_pos, "diff": diff})
        comparativo[participant] = {"rows": rows, "total": total}
    return comparativo

# ---------------------------------------------------
# Rotas Flask
# ---------------------------------------------------

@app.route("/")
def index():
    standings = get_real_standings()
    predictions = load_predictions()
    scores = calculate_scores(predictions, standings)
    ranked = sorted(scores.items(), key=lambda x: x[1])
    return render_template_string(INDEX_TEMPLATE, standings=standings, participants=ranked)


@app.route("/comparativo")
def comparativo():
    standings = get_real_standings()
    predictions = load_predictions()
    comp = build_comparativo(predictions, standings)
    return render_template_string(COMPARATIVE_TEMPLATE, comparativo=comp)

# ---------------------------------------------------
# Execução
# ---------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True, port=PORT)
