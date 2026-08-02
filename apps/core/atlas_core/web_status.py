"""Минимальный web-status (Master Spec §33 Result, §28 Ember).

Генерирует автономную HTML-страницу со здоровьем профилей БЕЗ идентичностей.
Это не полный дашборд (он вне scope VP-0) — только честный статус: alias,
provider, state, права root, auth-state, capacity=UNKNOWN.
"""

from __future__ import annotations

import html


def _badge(ok: bool | None, yes: str, no: str, unknown: str = "UNKNOWN") -> str:
    if ok is None:
        return f'<span class="badge b-unknown">{unknown}</span>'
    return f'<span class="badge {"b-ok" if ok else "b-warn"}">{yes if ok else no}</span>'


def render_web_status(snapshot: dict) -> str:
    rows = []
    for p in snapshot.get("profiles", []):
        rows.append(
            "<tr>"
            f"<td>{html.escape(p['alias'])}</td>"
            f"<td>{html.escape(p['provider'])}</td>"
            f"<td>{html.escape(str(p['state']))}</td>"
            f"<td>{_badge(p['root_is_0700'], '0700', p.get('root_mode') or '?')}</td>"
            f"<td>{_badge(p['authenticated'], 'READY', html.escape(str(p['auth_state'])))}</td>"
            f"<td><span class=\"badge b-unknown\">{html.escape(p['capacity']['status'])}</span></td>"
            "</tr>"
        )
    if not rows:
        rows.append('<tr><td colspan="6">Профилей нет. Создайте изолированные root.</td></tr>')
    tools = snapshot["host"]["tools"]
    tool_rows = "".join(f"<tr><td>{html.escape(k)}</td><td>{html.escape(str(v))}</td></tr>"
                        for k, v in tools.items())
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CodeVinci Atlas — статус VP-0</title>
<style>
  :root {{ --bg:#0b0b0c; --surface:#171719; --border:#2a2928; --text:#f2eee8;
           --muted:#aaa49c; --ember:#f28a3d; --ok:#54b982; --warn:#e2a84b; --info:#6e9ecf; }}
  @media (prefers-color-scheme: light) {{
    :root {{ --bg:#f7f5f1; --surface:#fff; --border:#e2ddd4; --text:#1b1a18; --muted:#6b655c; }}
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--text); font:15px/1.5 system-ui,sans-serif; padding:24px; }}
  h1 {{ font-size:20px; margin:0 0 4px; }} h2 {{ font-size:15px; color:var(--muted); margin:24px 0 8px; }}
  .sub {{ color:var(--muted); margin:0 0 16px; }}
  .card {{ background:var(--surface); border:1px solid var(--border); border-radius:10px; padding:16px; overflow-x:auto; }}
  table {{ border-collapse:collapse; width:100%; min-width:520px; }}
  th,td {{ text-align:left; padding:8px 10px; border-bottom:1px solid var(--border); font-size:14px; }}
  th {{ color:var(--muted); font-weight:600; }}
  .badge {{ display:inline-block; padding:1px 8px; border-radius:999px; font-size:12px; font-weight:600; }}
  .b-ok {{ background:color-mix(in srgb, var(--ok) 22%, transparent); color:var(--ok); }}
  .b-warn {{ background:color-mix(in srgb, var(--warn) 22%, transparent); color:var(--warn); }}
  .b-unknown {{ background:color-mix(in srgb, var(--muted) 22%, transparent); color:var(--muted); }}
  .note {{ color:var(--muted); font-size:13px; margin-top:16px; }}
  .accent {{ color:var(--ember); }}
</style></head>
<body>
  <h1><span class="accent">CodeVinci Atlas</span> — статус профилей (VP-0)</h1>
  <p class="sub">Собрано: {html.escape(snapshot['host']['collected_at'])} · без email, token, cookie и raw path</p>
  <div class="card">
    <table>
      <thead><tr><th>Alias</th><th>Провайдер</th><th>Состояние</th><th>Root</th><th>Auth</th><th>Ёмкость</th></tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
  </div>
  <h2>Инструменты хоста</h2>
  <div class="card"><table><tbody>{tool_rows}</tbody></table></div>
  <p class="note">Ёмкость показана как UNKNOWN намеренно: провайдеры не отдают остаток лимита через стабильный источник (Master Spec §11.6). {html.escape(snapshot.get('note',''))}</p>
</body></html>"""
