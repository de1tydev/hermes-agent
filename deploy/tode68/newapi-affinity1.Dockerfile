FROM tode/hermes-agent:tode68-20260902-feishu-shared-session1

COPY --chown=10000:10000 --chmod=0444 \
  deploy/tode68/model_providers/tode/__init__.py \
  deploy/tode68/model_providers/tode/plugin.yaml \
  /opt/hermes/plugins/model-providers/tode/
