"""Keep spawned query workers from importing the HTTP server and its event loop."""

if __name__ == "__main__":
    from .server import main

    main()
