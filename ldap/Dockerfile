FROM alpine:3.20

RUN apk add --no-cache \
    bash \
    ca-certificates \
    openldap \
    openldap-back-mdb \
    openldap-clients \
    openssl

COPY bootstrap.sh /usr/local/bin/bootstrap.sh
RUN chmod +x /usr/local/bin/bootstrap.sh

EXPOSE 389 636

CMD ["/usr/local/bin/bootstrap.sh"]
