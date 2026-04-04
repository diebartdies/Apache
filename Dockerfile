FROM httpd:2.4-bookworm

# Install Python for CGI scripts and web.py app (plus ddclient for optional EasyDNS DDNS)
RUN apt-get update \
  && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       python3 python3-pip python3-venv \
       ddclient \
       libio-socket-ssl-perl \
  && rm -rf /var/lib/apt/lists/*

# PEP 668: install non-Debian Python packages into a virtualenv
RUN python3 -m venv /opt/venv \
  && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
  && /opt/venv/bin/pip install --no-cache-dir web.py waitress ansible ldap3 "psycopg[binary]"

# Add ansible binaries to PATH by symlinking to /usr/local/bin
RUN ln -s /opt/venv/bin/ansible* /usr/local/bin/

# Add CGI config for running .py scripts
COPY conf/extra/python-cgi.conf /usr/local/apache2/conf/extra/python-cgi.conf
RUN echo "\nInclude conf/extra/python-cgi.conf" >> /usr/local/apache2/conf/httpd.conf

# Reverse-proxy config to the web.py app
COPY conf/extra/webpy-proxy.conf /usr/local/apache2/conf/extra/webpy-proxy.conf
RUN echo "\nInclude conf/extra/webpy-proxy.conf" >> /usr/local/apache2/conf/httpd.conf

# Static mapping for serving album audio files
COPY conf/extra/albums-static.conf /usr/local/apache2/conf/extra/albums-static.conf
RUN echo "\nInclude conf/extra/albums-static.conf" >> /usr/local/apache2/conf/httpd.conf

# Listen on 8585 over plain HTTP
COPY conf/extra/http-8585.conf /usr/local/apache2/conf/extra/http-8585.conf
RUN echo "\nInclude conf/extra/http-8585.conf" >> /usr/local/apache2/conf/httpd.conf

# Copy site + CGI scripts
COPY htdocs/ /usr/local/apache2/htdocs/
COPY cgi-bin/ /usr/local/apache2/cgi-bin/

# Copy web.py app
COPY webapp/ /opt/webapp/

# Entrypoint that runs both services
COPY start-webpy.sh /usr/local/bin/start-webpy.sh

# Fix Windows CRLF line endings so shebangs work in Linux containers
# and ensure scripts are executable.
RUN sed -i 's/\r$//' /usr/local/apache2/cgi-bin/*.py \
  && chmod 755 /usr/local/apache2/cgi-bin/*.py

RUN sed -i 's/\r$//' /usr/local/bin/start-webpy.sh /opt/webapp/*.py \
  && chmod 755 /usr/local/bin/start-webpy.sh

CMD ["/usr/local/bin/start-webpy.sh"]
