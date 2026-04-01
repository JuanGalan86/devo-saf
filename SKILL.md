---
name: calculo-saf
description: Ejecutar consultas de Devo usando el endpoint https://apiv2-sasr.devo.com/search/query a partir de un catalogo YAML local. Use this skill when Codex needs to listar consultas disponibles, resolver una consulta por titulo exacto o aproximado, pedir al usuario que elija entre varias coincidencias, rellenar parametros como {{csl}} o {{dial}}, ajustar ventanas temporales naturales a valores from/to de Devo y devolver una tabla resumida con los resultados.
---

# Calculo SAF

Usar esta skill para trabajar con un catalogo manual de consultas Devo y ejecutarlas por titulo.

## Flujo

1. Leer [`references/query_catalog.yaml`](./references/query_catalog.yaml) para localizar la consulta.
2. Si el usuario quiere explorar, listar las consultas con `python3 scripts/devo_query.py list`.
3. Si el usuario pide una consulta concreta, buscar primero coincidencia exacta y luego aproximada.
4. Si hay varias coincidencias razonables, mostrar la lista de opciones y pedir que elija una.
5. Resolver los placeholders `{{parametro}}` con los valores dados por el usuario o con los `default` del catalogo.
6. Traducir fechas naturales a los argumentos `--from` y `--to` que entiende Devo. Ejemplos:
   `ultimas 24 horas` -> `--from 1d --to now`
   `ultimos 7 dias` -> `--from 7d --to now`
   `de 2026-03-01 00:00 a 2026-03-02 00:00` -> pasar esos valores tal cual
7. Ejecutar la consulta con `python3 scripts/devo_query.py run ...`.
8. Devolver un resumen corto con nombre, periodo, numero de filas y la tabla previa.

## Comandos

Listar consultas:

```bash
python3 scripts/devo_query.py list
python3 scripts/devo_query.py list --filter guia
```

Ejecutar una consulta sin parametros:

```bash
python3 scripts/devo_query.py run --title "Guía TV" --from 1d --to now
```

Ver el JSON crudo:

```bash
python3 scripts/devo_query.py run --title "Guía TV" --from 1d --to now --raw
```

Ejecutar una consulta con parametros:

```bash
python3 scripts/devo_query.py run \
  --title "Audiencias multicast" \
  --from 7d \
  --to now \
  --param canal_elegido=24 \
  --param dial_elegido=24 \
  --param min_clientes=10
```

## Catalogo

Mantener el catalogo en [`references/query_catalog.yaml`](./references/query_catalog.yaml).

Cada entrada debe incluir:

- `id`
- `title`
- `description`
- `parameters`
- `query`

Usar placeholders `{{nombre_parametro}}` dentro de la query.

## Configuracion

La configuracion vive en [`references/devo_config.yaml`](./references/devo_config.yaml).

- `auth_token` puede quedarse en el fichero mientras trabajas asi.
- `DEVO_AUTH_TOKEN` tiene prioridad sobre el fichero para poder migrar luego sin tocar el catalogo ni el script.
- No cambiar el `endpoint` salvo que el usuario lo pida explicitamente.

## Manejo De Errores

Si falla la ejecucion:

- revisar si falta token
- revisar si faltan placeholders obligatorios
- revisar si la consulta no existe
- revisar HTTP `401`, `403`, `404` o `5xx`
- revisar si la respuesta no trae filas o viene en un formato inesperado

Cuando el script liste varias coincidencias, no inventar una eleccion: ensenar las opciones al usuario.
