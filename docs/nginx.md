# Generate Nginx configuration

Nginx terminates external HTTPS and proxies HTTP to `127.0.0.1:<server.port>`.
The application must bind `127.0.0.1` or `0.0.0.0`. Configure the deployment
listener and certificate paths in the service `.lclcfg`:

```text
nginx.server_name: "api.example.com"
nginx.listen_port: 443
nginx.ssl_certificate: "/etc/pki/tls/certs/example.crt"
nginx.ssl_certificate_key: "/etc/pki/tls/private/example.key"
```

```sh
lcl-fastapi nginx render -o config service.lclcfg
lcl-fastapi nginx render -o config service.lclcfg -o output example.conf
```

The result includes forwarded Host, client-IP, and scheme headers. The exact
`/_lcl/shutdown` path returns 404 at the proxy. The upstream URI is preserved;
the renderer adds no route prefix or rewrite. `server.root_path` is external
origin metadata, such as `https://public.example.com:8443`; it does not override
Nginx's explicit listener or domain. A deployment using NAT or another proxy is
responsible for keeping that public origin accurate.

Certificate paths must be absolute POSIX paths and cannot contain `$` variable
syntax or control characters. Rendering validates text only: it does not read
certificates, contact the server, execute `nginx -t`, install files into a system
directory, or reload Nginx. Review and deploy the generated file separately.

The pure renderer also works on Windows and does not require Nginx:

<!-- python-doc-exec -->
```python
from lcl_fastapi.render import NginxSettings, render_nginx

settings = NginxSettings(
    server_name="api.example.com",
    listen_port=443,
    service_port=8080,
    certificate="/certs/service.crt",
    certificate_key="/certs/service.key",
)
configuration = render_nginx(settings)
assert "proxy_pass http://127.0.0.1:8080;" in configuration
assert "location = /_lcl/shutdown { return 404; }" in configuration
```

[CLI contract](cli.md) · [Nginx proxy_pass semantics](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_pass)
