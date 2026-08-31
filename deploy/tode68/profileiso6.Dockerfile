FROM tode/hermes-agent:tode68-20260831-profileiso5

COPY --chown=10000:10000 --chmod=0444 \
  gateway/profile_provisioning.py \
  /opt/hermes/gateway/profile_provisioning.py
