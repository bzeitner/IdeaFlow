# Gephi Lite upstream

Graph Lab embeds a production build of Gephi Lite, version 1.0.2,
from commit `d47ecb459a00e2942ee0c2b8d6630015124b9ff4` at
<https://github.com/gephi/gephi-lite>. Gephi Lite is licensed under GPL-3.0.

The upstream files are built at release time and are not committed here. Use
`deploy/build_graph_lab.sh`; it verifies the exact source commit, installs from
the upstream lockfile, builds with `/gephi/` as its base path, and produces a
checksummed artifact for `deploy/install_graph_lab.sh`.

The build step removes upstream Google Fonts imports so the deployed UI remains
self-hosted and works with Graph Lab's restrictive content security policy. The
existing CSS fallback font stacks are left intact.
