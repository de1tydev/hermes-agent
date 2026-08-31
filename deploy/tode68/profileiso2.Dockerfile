FROM tode/hermes-agent:tode68-20260829-profileiso1

COPY --chown=10000:10000 --chmod=0444 \
  agent/profile_sandbox.py \
  /opt/hermes/agent/profile_sandbox.py
