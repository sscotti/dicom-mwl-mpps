# DICOM MWL-MPPS Microservice

A standalone DICOM Modality Worklist (MWL) and Modality Performed Procedure Step (MPPS) server with REST API.

## Features

- ✅ MWL C-FIND SCP
- ✅ MPPS N-CREATE/N-SET SCP  
- ✅ FastAPI REST interface
- ✅ MySQL database backend
- ✅ Docker containerized
- ✅ Cross-platform (Apple Silicon + Intel)

## License

GPLv3 - Medical imaging software

## Architecture

- **dicom-mwl-mpps**: Python service for DICOM MWL/MPPS (DIMSE) using pynetdicom/pydicom.
- **mwl-api**: FastAPI service for creating MWL entries via REST API.
- **mysql_db**: MySQL database for persistent MWL storage.
- **worklist/**: Shared folder for DICOM .wl files, accessible by both services.

## Quick Start

1. **Clone the repository**
2. **Set up your `.env` file** (see below)
3. **Start the stack:**

   ```sh
   docker compose up --build
   ```

4. **Test the API:**

   ```sh
   curl -X POST http://localhost:8000/mwl/create_from_json \
     -H "Content-Type: application/json" \
     -d '{"AccessionNumber": "TEST123",
          "PatientID": "P1",
          "PatientName": "DOE^JOHN",
          "PatientBirthDate": "19800101",
          "PatientSex": "M",
          "StudyInstanceUID": "1.2.3.4.5",
          "ScheduledProcedureStepSequence": [
          {
          "ScheduledProcedureStepStartDate": "20240625",
          "ScheduledStationAETitle": "ORTHANC"
          }
        ]
        }'
    ```

5. **Test C-Find Worklist**

   findscu localhost 4104 -W -v -d -k "AccessionNumber" -k "PatientName" -k "PatientID"
6. **Test N-SET and N-CREATE**

# Generate the test files and client

python3 create_test_mpps.py

# Test using the custom pynetdicom client, 4104 on the host

python3 test_mpps/test_mpps_client.py localhost 104 test_mpps/mpps_create.dcm
python3 test_mpps/test_mpps_client.py localhost 104 test_mpps/mpps_set.dcm

## Environment Variables

Set these in your `.env` file:

```

DB_HOST=mysql_db
DB_USER=root
DB_PASSWORD=root
DB_NAME=orthanc_ris
WORKLIST_DIR=/worklist

```

## MySQL Initialization

- Place your schema (e.g., `orthanc_ris.sql`) in `mysql-init/`.
- The DB is auto-initialized on first run.

## Cross-Platform Support

- Uses multi-arch Docker images for MySQL and Python.
- Works on Apple Silicon (M1/M2) and Intel.

## License

This project is licensed under the GNU General Public License v3.0 (GPLv3). See [LICENSE](LICENSE) for details.
