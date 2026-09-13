# media/examples/

Content shown by the bot's "👀 Посмотреть примеры контента" button
(`show_examples` in `app/handlers.py`). Files here are uploaded directly
to the server — never committed to the repo — and are picked up
immediately, with no code change or rebuild.

## Naming convention

Name files `example_<n>.<ext>`, where `<n>` is `1` through `5` and
`<ext>` is `jpg` or `mp4`. Both the index and the extension are
optional: any missing file is skipped silently, and files are sent in
numeric order (photo before video when both exist for the same index).

Examples:

```
example_1.jpg
example_1.mp4
example_2.jpg
example_3.mp4
```

If this directory has no matching files at all, the bot falls back to
a placeholder text so the button never breaks.
