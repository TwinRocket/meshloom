# Built on the image published for every release rather than pulled as-is: an
# add-on that names `image:` in its config is run exactly as built, so nothing
# would translate the options Home Assistant writes and every one of them would be
# silently ignored. One layer adds that translation.
#
# The tag is rewritten by scripts/build/publish.sh alongside the add-on's version,
# so the add-on cannot advertise one release and install another.
FROM ghcr.io/bagl3y/meshloom:4.10.0

COPY run.py /usr/local/bin/meshloom-addon
RUN chmod +x /usr/local/bin/meshloom-addon

CMD ["/usr/local/bin/meshloom-addon"]
