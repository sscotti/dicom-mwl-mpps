# DICOM MWL-MPPS

DICOM Modality Worklist (MWL) and Modality Performed Procedure Step (MPPS) service, with a small REST API and web dashboard.

## Services

| Service | Role | Host port |
|---------|------|-----------|
| `dicom-mwl-mpps` | MWL C-FIND and MPPS N-CREATE / N-SET | `4104` (container port 104) |
| `mwl-api` | REST API and HTML dashboard | `8000` |
| `mysql_db` | MySQL 26.10.0 storage for MWL and MPPS | `3306` |

Worklist files are written to `worklist/` and mounted into both application containers at `/worklist`.

Images are pinned in `docker-compose.yml` and the Dockerfiles: Python 3.11.18, MySQL 26.10.0, and the package versions in each `requirements.txt`.

## Start

Docker and Docker Compose are required. `make` is optional.

`.env.sample` is the configuration template. This checkout already has a `.env` copied from it. On a fresh clone:

```bash
cp .env.sample .env
```

Compose reads `DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`, and `WORKLIST_DIR` from that file.

```bash
make dev-setup
```

That builds the images and starts the stack with `docker-compose.override.yml`. The override mounts the service source, reloads the API when its code changes, and adds the test runner. Restart the DICOM container after editing its code. `make production` starts `docker-compose.yml` only.

- Dashboard: http://localhost:8000
- API docs: http://localhost:8000/docs
- DICOM: `localhost:4104`

## API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Dashboard |
| `/health` | GET | Database connectivity |
| `/mwl` | GET | MWL list |
| `/mpps` | GET | MPPS list |
| `/mwl/create_from_json` | POST | Create an MWL entry from JSON |
| `/docs` | GET | OpenAPI UI |

```bash
curl -X POST http://localhost:8000/mwl/create_from_json \
  -H "Content-Type: application/json" \
  -d '{
    "AccessionNumber": "TEST123",
    "PatientID": "P1",
    "PatientName": "DOE^JOHN",
    "PatientBirthDate": "19800101",
    "PatientSex": "M",
    "StudyInstanceUID": "1.2.3.4.5",
    "ScheduledProcedureStepSequence": [{
      "ScheduledProcedureStepStartDate": "20240625",
      "ScheduledStationAETitle": "ORTHANC"
    }]
  }'
```

A C-FIND against the worklist, if `findscu` is installed:

```bash
findscu localhost 4104 -W -k AccessionNumber -k PatientName -k PatientID
```

## Tests

```bash
make test
```

That runs `docker/mwl-mpps/tests/test_mpps_integration.py` inside Compose: create an MWL entry, N-CREATE an MPPS, N-SET it to completed, and confirm the worklist query drops the completed entry.

`make test-manual` posts one MWL entry and runs `findscu` when that tool is on the host.

## Make targets

```bash
make help
make dev-setup     # build and start with the development override
make production    # start from docker-compose.yml
make health
make logs
make shell         # DICOM container
make shell-api
make db-shell
make db-reset      # deletes the MySQL volume
make stop
```

## Database

`mysql-init/` is applied only when the data volume is empty:

- `mwl` — worklist rows and the DICOM dataset blob
- `mpps` — performed procedure steps
- `dicom_tags` — DICOM tag reference data

## Troubleshooting

```bash
make health
make logs
make db-reset      # reinitializes MySQL; existing rows are deleted
make clean         # stop and remove volumes
```

If port 3306, 4104, or 8000 is already taken, stop the other process or change the host mapping in `docker-compose.yml`.

## License

GNU General Public License v3.0. See [LICENSE](LICENSE).
