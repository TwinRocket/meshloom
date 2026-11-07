def on_message(params):
    if params["how"] == "code":

        def f():
            return 1

        f.__code__ = (lambda: 2).__code__
        return f()

    class A:
        pass

    class B:
        pass

    a = A()
    a.__class__ = B
    return type(a).__name__
