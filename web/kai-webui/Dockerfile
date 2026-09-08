FROM nginx:1.30.3-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY dist /usr/share/nginx/html
RUN chmod -R a+rX /usr/share/nginx/html

EXPOSE 80
HEALTHCHECK --interval=15s --timeout=5s --start-period=5s --retries=6 CMD nginx -t || exit 1
CMD ["nginx", "-g", "daemon off;"]
