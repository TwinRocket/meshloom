def on_message(params):
    if params["how"] == "urllib":
        import urllib.request

        return urllib.request.urlopen("http://127.0.0.1:9/").status
    import http.client

    return repr(http.client.HTTPConnection("127.0.0.1", 9))
