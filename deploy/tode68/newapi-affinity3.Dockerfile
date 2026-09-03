FROM tode/hermes-agent:tode68-20260903-newapi-affinity2

COPY --chown=10000:10000 --chmod=0444 \
  agent/auxiliary_client.py \
  /opt/hermes/agent/auxiliary_client.py

COPY --chown=10000:10000 --chmod=0444 \
  gateway/run.py \
  /opt/hermes/gateway/run.py

COPY --chown=10000:10000 --chmod=0444 \
  deploy/tode68/model_providers/tode/__init__.py \
  deploy/tode68/model_providers/tode/plugin.yaml \
  /opt/hermes/plugins/model-providers/tode/

COPY --chown=10000:10000 --chmod=0555 \
  scripts/configure_tode68_newapi_affinity.py \
  /opt/hermes/scripts/configure_tode68_newapi_affinity.py
