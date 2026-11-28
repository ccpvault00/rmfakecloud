# rmsync

Two-way, run-when-you-want sync between a local folder and one top-level folder on the
reMarkable, through this rmfakecloud server's web-UI API.

| Change | Result |
|---|---|
| new / edited PDF or EPUB locally | uploaded (an edited file replaces the tablet copy; the old one goes to the tablet trash) |
| handwriting / highlights on the tablet (PDF) | drawn into the local PDF, **replacing it** (tablet page order, inserted pages included) |
| new notebook or document on the tablet | written locally (notebooks as PDF) |
| move / rename on either side | mirrored |
| delete on either side | mirrored: tablet deletions go to `trash_dir`, local ones to the tablet trash |
| both sides changed | local edit kept as `name (conflict YYYY-MM-DD).pdf` (and uploaded), tablet version takes the name |

Not covered: EPUB annotations are not written back (noted); typed text is not rendered;
writing beyond the page edge is clipped; edits to an exported notebook PDF are not pushed back.

## Use

```bash
pip install --no-deps tools/rmsync      # needs pymupdf + pyyaml (environment.yml)
rmsync --init                           # writes ~/.config/rmsync/config.yaml
printf '%s\n' 'web-UI password' > ~/.config/rmsync/password && chmod 600 ~/.config/rmsync/password
rmsync --dry-run                        # what would happen
rmsync                                  # do it
```

Several folder pairs: one YAML file per pair in `~/.config/rmsync/` (copy `config.yaml`, change
`local_root`, `remote_folder`, `state_file`, `trash_dir`). Plain `rmsync` syncs every `*.yaml` there,
`config.yaml` first; `rmsync --config FILE` syncs one. Profiles that share a tablet folder, a state or
trash path, or nested local folders are refused before anything runs.

Safety: the run stops before changing anything when the local folder is missing or has
no PDFs/EPUBs although files were synced before, or when more than `max_deletes`
deletions are planned (`--allow-deletes` to confirm). State: `state_file` (JSON, saved after
every action, so an interrupted run resumes). Losing it is safe: same-path files are linked, not re-uploaded.

## Develop

```bash
cd tools/rmsync && ruff check . && mypy src && pytest -v   # live test needs dist/rmfakecloud-x64
```

`render.py` ports `ui/src/pages/Documents/strokes.js`; keep pen models and `PT_PER_UNIT` in step.
