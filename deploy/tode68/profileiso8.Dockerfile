FROM tode/hermes-agent:tode68-20260831-profileiso6

COPY --chown=10000:10000 --chmod=0444 \
  gateway/run.py \
  /opt/hermes/gateway/run.py
