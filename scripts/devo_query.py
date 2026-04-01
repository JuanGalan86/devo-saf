#!/usr/bin/env python3
"""List and execute Devo queries from a local YAML catalog."""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import subprocess
import sys
import textwrap
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "references" / "query_catalog.yaml"
CONFIG_PATH = ROOT / "references" / "devo_config.yaml"
PLACEHOLDER_RE = re.compile(r"\{\{([a-zA-Z0-9_]+)\}\}")


class QueryToolError(Exception):
    """Controlled error for user-facing failures."""


def load_yaml(path: Path) -> Any:
    """Load YAML without requiring PyYAML in the local environment."""
    try:
        import yaml  # type: ignore
    except ImportError:
        command = [
            "ruby",
            "-ryaml",
            "-rjson",
            "-e",
            "print JSON.generate(YAML.safe_load(ARGF.read, permitted_classes: [], aliases: false))",
            str(path),
        ]
        try:
            completed = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            raise QueryToolError("No se encontro Ruby para leer los ficheros YAML.") from exc
        except subprocess.CalledProcessError as exc:
            raise QueryToolError(
                f"No se pudo leer el YAML {path.name}: {exc.stderr.strip() or exc.stdout.strip()}"
            ) from exc
        return json.loads(completed.stdout)
    else:
        with path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle)


def load_config() -> dict[str, Any]:
    config = load_yaml(CONFIG_PATH) or {}
    if not isinstance(config, dict):
        raise QueryToolError("El fichero devo_config.yaml no tiene un formato valido.")
    return config


def load_catalog() -> list[dict[str, Any]]:
    catalog = load_yaml(CATALOG_PATH) or {}
    queries = catalog.get("queries", [])
    if not isinstance(queries, list):
        raise QueryToolError("El fichero query_catalog.yaml no contiene una lista de consultas valida.")
    return queries


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def list_queries(queries: list[dict[str, Any]], text_filter: str | None) -> int:
    filtered = []
    for entry in queries:
        haystack = " ".join(
            [
                str(entry.get("title", "")),
                str(entry.get("id", "")),
                str(entry.get("description", "")),
            ]
        ).lower()
        if not text_filter or text_filter.lower() in haystack:
            filtered.append(entry)

    if not filtered:
        print("No hay consultas que coincidan con ese filtro.")
        return 0

    print(f"Consultas disponibles: {len(filtered)}")
    for entry in filtered:
        parameters = entry.get("parameters") or []
        param_names = ", ".join(param["name"] for param in parameters) if parameters else "sin parametros"
        print(f"- {entry.get('title')} [{entry.get('id')}]")
        print(f"  {entry.get('description', '').strip()}")
        print(f"  Parametros: {param_names}")
    return 0


def choose_query(queries: list[dict[str, Any]], requested_title: str) -> dict[str, Any]:
    exact_matches = []
    normalized_requested = normalize(requested_title)

    for entry in queries:
        for candidate in (entry.get("title", ""), entry.get("id", "")):
            if normalize(str(candidate)) == normalized_requested:
                exact_matches.append(entry)
                break

    if len(exact_matches) == 1:
        return exact_matches[0]

    candidates = []
    scored: list[tuple[float, dict[str, Any]]] = []
    for entry in queries:
        title = str(entry.get("title", ""))
        score = difflib.SequenceMatcher(None, normalize(title), normalized_requested).ratio()
        if normalized_requested and normalize(title).find(normalized_requested) != -1:
            score = max(score, 0.95)
        scored.append((score, entry))

    for score, entry in sorted(scored, key=lambda item: item[0], reverse=True):
        if score >= 0.45:
            candidates.append(entry)
        if len(candidates) == 5:
            break

    unique_candidates = []
    seen_ids = set()
    for entry in candidates:
        entry_id = entry.get("id")
        if entry_id not in seen_ids:
            unique_candidates.append(entry)
            seen_ids.add(entry_id)

    if not unique_candidates:
        raise QueryToolError(f'No encontre ninguna consulta parecida a "{requested_title}".')

    if len(unique_candidates) > 1:
        lines = [f'Hay varias consultas parecidas a "{requested_title}":']
        for entry in unique_candidates:
            lines.append(f"- {entry.get('title')} [{entry.get('id')}]")
        lines.append("Elige una de la lista y vuelve a ejecutar la orden con el titulo exacto o mas preciso.")
        raise QueryToolError("\n".join(lines))

    return unique_candidates[0]


def parse_params(param_pairs: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for pair in param_pairs:
        if "=" not in pair:
            raise QueryToolError(f'Parametro invalido "{pair}". Usa el formato clave=valor.')
        key, value = pair.split("=", 1)
        key = key.strip()
        if not key:
            raise QueryToolError(f'Parametro invalido "{pair}". La clave no puede estar vacia.')
        values[key] = value
    return values


def resolve_query(query_entry: dict[str, Any], cli_params: dict[str, str]) -> tuple[str, dict[str, str]]:
    parameters = query_entry.get("parameters") or []
    parameter_values: dict[str, str] = {}
    for parameter in parameters:
        name = parameter["name"]
        if name in cli_params:
            parameter_values[name] = cli_params[name]
        elif "default" in parameter:
            parameter_values[name] = str(parameter.get("default", ""))
        else:
            raise QueryToolError(
                f'Falta el parametro obligatorio "{name}" para la consulta "{query_entry.get("title")}".'
            )

    query_text = str(query_entry.get("query", ""))
    missing = sorted(set(PLACEHOLDER_RE.findall(query_text)) - set(parameter_values))
    if missing:
        raise QueryToolError(
            "Faltan placeholders por resolver: " + ", ".join(missing)
        )

    def replace(match: re.Match[str]) -> str:
        return parameter_values[match.group(1)]

    rendered_query = PLACEHOLDER_RE.sub(replace, query_text)
    return rendered_query, parameter_values


def resolve_auth_token(config: dict[str, Any]) -> str:
    token = os.environ.get("DEVO_AUTH_TOKEN") or str(config.get("auth_token", "")).strip()
    if not token:
        raise QueryToolError(
            "No hay token configurado. Rellena auth_token en devo_config.yaml o exporta DEVO_AUTH_TOKEN."
        )
    return token


def execute_query(
    endpoint: str,
    token: str,
    query: str,
    date_from: str,
    date_to: str,
    timeout_seconds: int,
) -> Any:
    payload = {
        "query": query,
        "from": date_from,
        "to": date_to,
        "mode": {"type": "json"},
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise QueryToolError(
            f"Error HTTP {exc.code} al consultar Devo: {detail[:500].strip()}"
        ) from exc
    except urllib.error.URLError as exc:
        raise QueryToolError(f"No se pudo conectar con Devo: {exc.reason}") from exc

    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        snippet = body[:500].strip()
        raise QueryToolError(f"La respuesta no es JSON valido: {snippet}") from exc


def extract_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]

    if not isinstance(payload, dict):
        return []

    for key in ("data", "rows", "results", "items", "result", "object"):
        value = payload.get(key)
        if isinstance(value, list) and all(isinstance(item, dict) for item in value):
            return value
        if isinstance(value, dict):
            nested_rows = extract_rows(value)
            if nested_rows:
                return nested_rows

    if isinstance(payload.get("tables"), list):
        for table in payload["tables"]:
            nested_rows = extract_rows(table)
            if nested_rows:
                return nested_rows

    if all(isinstance(value, (str, int, float, bool, type(None))) for value in payload.values()):
        return [payload]

    return []


def format_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.2f}".rstrip("0").rstrip(".")
    return str(value).replace("\n", " ")


def render_markdown_table(rows: list[dict[str, Any]], row_limit: int) -> str:
    if not rows:
        return "Sin filas en la respuesta."

    preview = rows[:row_limit]
    headers = list(preview[0].keys())
    for row in preview[1:]:
        for key in row.keys():
            if key not in headers:
                headers.append(key)

    matrix = [[format_value(row.get(header, "")) for header in headers] for row in preview]
    widths = []
    for index, header in enumerate(headers):
        widths.append(max(len(header), *(len(row[index]) for row in matrix)))

    def render_line(values: list[str]) -> str:
        return "| " + " | ".join(value.ljust(widths[index]) for index, value in enumerate(values)) + " |"

    separator = "| " + " | ".join("-" * width for width in widths) + " |"
    lines = [render_line(headers), separator]
    lines.extend(render_line(row) for row in matrix)
    return "\n".join(lines)


def print_summary(
    query_entry: dict[str, Any],
    rendered_query: str,
    date_from: str,
    date_to: str,
    rows: list[dict[str, Any]],
    row_limit: int,
) -> None:
    print(f"Consulta: {query_entry.get('title')}")
    print(f"Periodo: {date_from} -> {date_to}")
    print(f"Filas: {len(rows)}")
    print("Query renderizada:")
    print(textwrap.indent(rendered_query, "  "))
    print("Tabla resumida:")
    print(render_markdown_table(rows, row_limit))


def print_raw_payload(payload: Any) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ejecutar consultas Devo desde un catalogo YAML.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="Listar consultas disponibles.")
    list_parser.add_argument("--filter", help="Filtrar por texto en titulo, id o descripcion.")

    run_parser = subparsers.add_parser("run", help="Ejecutar una consulta del catalogo.")
    run_parser.add_argument("--title", required=True, help="Titulo o texto aproximado de la consulta.")
    run_parser.add_argument("--from", dest="date_from", help="Valor from para Devo.", default=None)
    run_parser.add_argument("--to", dest="date_to", help="Valor to para Devo.", default=None)
    run_parser.add_argument(
        "--param",
        action="append",
        default=[],
        help="Parametro en formato clave=valor. Repetible.",
    )
    run_parser.add_argument(
        "--rows",
        type=int,
        default=None,
        help="Numero maximo de filas a mostrar en la tabla resumida.",
    )
    run_parser.add_argument(
        "--raw",
        action="store_true",
        help="Mostrar el JSON crudo devuelto por Devo.",
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        config = load_config()
        queries = load_catalog()

        if args.command == "list":
            return list_queries(queries, args.filter)

        query_entry = choose_query(queries, args.title)
        cli_params = parse_params(args.param)
        rendered_query, _resolved_params = resolve_query(query_entry, cli_params)

        token = resolve_auth_token(config)
        endpoint = str(config.get("endpoint", "")).strip()
        if not endpoint:
            raise QueryToolError("No hay endpoint configurado en devo_config.yaml.")

        date_from = args.date_from or str(config.get("default_from", "1d"))
        date_to = args.date_to or str(config.get("default_to", "now"))
        timeout_seconds = int(config.get("timeout_seconds", 60))
        row_limit = args.rows or int(config.get("row_preview_limit", 10))

        payload = execute_query(endpoint, token, rendered_query, date_from, date_to, timeout_seconds)
        if args.raw:
            print_raw_payload(payload)
            return 0
        rows = extract_rows(payload)
        print_summary(query_entry, rendered_query, date_from, date_to, rows, row_limit)
        return 0

    except QueryToolError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
