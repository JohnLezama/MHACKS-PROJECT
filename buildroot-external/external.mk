# Application files and offline model assets are installed by post-build.sh.
# Metadata retrieval requires FTS5 even in releases with no dedicated Kconfig switch.
SQLITE_CONF_OPTS += --enable-fts5
