FROM tode/hermes-agent:tode68-20260831-profileiso2

COPY --chown=10000:10000 --chmod=0444 \
  plugins/platforms/feishu/adapter.py \
  /opt/hermes/plugins/platforms/feishu/adapter.py
