# devo-saf

Herramienta local para listar y ejecutar consultas de Devo desde un catalogo YAML.

## Requisitos

- Python 3
- Un token de Devo en la variable de entorno `DEVO_AUTH_TOKEN`

## Uso

Listar consultas:

```bash
python3 scripts/devo_query.py list
python3 scripts/devo_query.py list --filter dns
```

Ejecutar una consulta:

```bash
export DEVO_AUTH_TOKEN="tu_token"
python3 scripts/devo_query.py run --title "Guía TV" --from 1d --to now
```

Consulta con parametros:

```bash
python3 scripts/devo_query.py run \
  --title "Audiencias multicast" \
  --from 7d \
  --to now \
  --param canal_elegido=24 \
  --param dial_elegido=24 \
  --param min_clientes=10
```