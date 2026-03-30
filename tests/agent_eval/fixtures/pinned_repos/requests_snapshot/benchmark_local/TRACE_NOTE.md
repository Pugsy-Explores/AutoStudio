# Session trace (benchmark)

When resolving redirects, the Requests session object consults **`hooks`** first,
then applies adapter send. The entry for ordinary GET calls is **`Session.request`**.
