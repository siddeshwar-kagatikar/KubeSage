# Bandwidth-limited pull-through proxy in front of the local registry.
# The egress cap is applied with tc (token bucket) so it limits aggregate throughput,
# not per connection (containerd downloads layers in parallel).
FROM nginx:1.27-alpine
RUN apk add --no-cache iproute2-tc
COPY nginx.conf /etc/nginx/nginx.conf
COPY entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
