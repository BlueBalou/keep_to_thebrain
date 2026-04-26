# Use default brain (your existing one)
.venv/bin/python keep_to_thebrain.py --export-dir ./test3/

# Use a different brain
.venv/bin/python keep_to_thebrain.py --export-dir ./test3/ --brain-id <uuid>

# Dry run against a different brain
.venv/bin/python keep_to_thebrain.py --export-dir ./test3/ --brain-id <uuid> --dry-run