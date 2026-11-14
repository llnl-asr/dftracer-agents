# What worked: dftracer_docs

## tool:docs_search
`docs_search(query, source="dftracer", max_results=5, fetch_content=True)`; with `fetch_content=False` for links only.

## tool:docs_fetch_page
`docs_fetch_page("<base>/c-api.html", query="function macros")` returns the top sections ranked by term frequency, with the first code block of each.
