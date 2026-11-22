#!/usr/bin/env python3
"""
Integration tests for DICOM MWL-MPPS functionality
"""

import pytest
from pynetdicom import AE
from pynetdicom.sop_class import ModalityPerformedProcedureStep, ModalityWorklistInformationFind
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid, PYDICOM_IMPLEMENTATION_UID
from pydicom import dcmread
from datetime import datetime, date
from io import BytesIO
import mysql.connector
import requests
from uuid import uuid4
import os
import socket

# Import the correct SOP Class UID
try:
    from pynetdicom.sop_class import ModalityPerformedProcedureStep
    MPPS_SOP_CLASS_UID = ModalityPerformedProcedureStep
except ImportError:
    MPPS_SOP_CLASS_UID = "1.2.840.10008.3.1.2.3.3"

# Configuration for different environments
def get_api_url():
    """Get API URL based on environment"""
    # Check if we're running inside a Docker container
    if os.path.exists('/.dockerenv'):
        return "http://mwl-api:8000"
    else:
        return "http://localhost:8000"

def get_dicom_host():
    """Get DICOM host based on environment"""
    # Check if we're running inside a Docker container
    if os.path.exists('/.dockerenv'):
        # Resolve to IP address to work around pynetdicom DNS issues
        try:
            return socket.gethostbyname('dicom-mwl-mpps')
        except socket.gaierror:
            return "dicom-mwl-mpps"  # Fallback to hostname
    else:
        return "localhost"

def get_dicom_port():
    """Get DICOM port based on environment"""
    return 104 if os.path.exists('/.dockerenv') else 4104

class TestData:
    """Test data factory for DICOM datasets"""
    
    @staticmethod
    def create_mwl_entry_json():
        """Create MWL entry via REST API"""
        return {
            "AccessionNumber": uuid4().hex[:16].upper(),
            "PatientID": "P1", 
            "PatientName": "DOE^JOHN",
            "PatientBirthDate": "19800101",
            "PatientSex": "M",
            "StudyInstanceUID": generate_uid(),
            "ScheduledProcedureStepSequence": [{
                "ScheduledProcedureStepStartDate": date.today().strftime("%Y%m%d"),
                "ScheduledStationAETitle": "ORTHANC"
            }]
        }
    
    @staticmethod
    def create_mpps_dataset(sop_instance_uid, worklist_entry, status="IN PROGRESS"):
        """Create MPPS dataset dynamically"""
        ds = Dataset()
        
        # File Meta Information
        file_meta = FileMetaDataset()
        file_meta.FileMetaInformationGroupLength = 0
        file_meta.FileMetaInformationVersion = b'\x00\x01'
        file_meta.MediaStorageSOPClassUID = MPPS_SOP_CLASS_UID
        file_meta.MediaStorageSOPInstanceUID = sop_instance_uid
        file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
        file_meta.ImplementationClassUID = PYDICOM_IMPLEMENTATION_UID
        file_meta.ImplementationVersionName = "PYDICOM"
        
        ds.file_meta = file_meta

        if status != "IN PROGRESS":
            # N-SET sends only the attributes that change at completion.
            modification = Dataset()
            modification.PerformedProcedureStepStatus = status
            modification.PerformedProcedureStepEndDate = date.today().strftime("%Y%m%d")
            modification.PerformedProcedureStepEndTime = datetime.now().strftime("%H%M%S")
            series_item = Dataset()
            series_item.SeriesInstanceUID = generate_uid()
            series_item.SeriesDescription = "Chest CT Images"
            series_item.Modality = "CT"
            series_item.OperatorsName = "TECH^JOHN"
            image_item = Dataset()
            image_item.ReferencedSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
            image_item.ReferencedSOPInstanceUID = generate_uid()
            series_item.ReferencedImageSequence = [image_item]
            modification.PerformedSeriesSequence = [series_item]
            return modification

        # SOP Class and Instance
        ds.SOPClassUID = MPPS_SOP_CLASS_UID
        ds.SOPInstanceUID = sop_instance_uid

        # Patient module. Study and accession belong in the scheduled-step sequence.
        ds.PatientName = "DOE^JOHN"
        ds.PatientID = "P1"
        ds.PatientBirthDate = "19800101"
        ds.PatientSex = "M"

        ds.PerformedProcedureStepID = "PPS001"
        ds.PerformedProcedureStepStatus = status
        ds.PerformedProcedureStepStartDate = date.today().strftime("%Y%m%d")
        ds.PerformedProcedureStepStartTime = datetime.now().strftime("%H%M%S")
        ds.Modality = "CT"
        ds.PerformedStationAETitle = "ORTHANC"
        ds.PerformedStationName = "CT_SCANNER_01"
        ds.PerformedProcedureStepDescription = "Chest CT Scan"

        sched_step_item = Dataset()
        sched_step_item.StudyInstanceUID = worklist_entry["StudyInstanceUID"]
        sched_step_item.AccessionNumber = worklist_entry["AccessionNumber"]
        sched_step_item.RequestedProcedureID = "REQ001"
        sched_step_item.ScheduledProcedureStepID = "SPS001"
        sched_step_item.ScheduledProcedureStepStartDate = worklist_entry[
            "ScheduledProcedureStepSequence"
        ][0]["ScheduledProcedureStepStartDate"]
        sched_step_item.ScheduledStationAETitle = "ORTHANC"
        ds.ScheduledStepAttributesSequence = [sched_step_item]
        return ds

def db_connect():
    return mysql.connector.connect(
        host=os.environ.get("DB_HOST", "mysql_db" if os.path.exists("/.dockerenv") else "127.0.0.1"),
        user=os.environ.get("DB_USER", "root"),
        password=os.environ.get("DB_PASSWORD", "root"),
        database=os.environ.get("DB_NAME", "orthanc_ris"),
    )

def fetch_mpps(sop_instance_uid):
    conn = db_connect()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT * FROM mpps WHERE sop_instance_uid = %s",
            (sop_instance_uid,),
        )
        row = cursor.fetchone()
    finally:
        cursor.close()
        conn.close()
    dataset = dcmread(BytesIO(row["dataset_blob"]), force=True) if row and row["dataset_blob"] else None
    return row, dataset

def fetch_mwl(accession_number):
    conn = db_connect()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT * FROM mwl WHERE AccessionNumber = %s",
            (accession_number,),
        )
        return cursor.fetchone()
    finally:
        cursor.close()
        conn.close()

class TestMWLMPPS:
    """Integration tests for MWL-MPPS functionality"""
    
    @pytest.fixture
    def setup_mwl_entry(self):
        """Create MWL entry via REST API before tests"""
        mwl_data = TestData.create_mwl_entry_json()
        api_url = get_api_url()
        
        print(f"Creating MWL entry via: {api_url}")
        response = requests.post(
            f"{api_url}/mwl/create_from_json",
            json=mwl_data,
            timeout=10,
        )
        assert response.status_code == 200, f"Failed to create MWL entry: {response.text}"
        return mwl_data
    
    def test_mpps_ncreate(self, setup_mwl_entry):
        """Test MPPS N-CREATE operation"""
        sop_instance_uid = generate_uid()
        ds = TestData.create_mpps_dataset(sop_instance_uid, setup_mwl_entry, "IN PROGRESS")
        
        dicom_host = get_dicom_host()
        dicom_port = get_dicom_port()
        print(f"Connecting to DICOM service: {dicom_host}:{dicom_port}")
        
        ae = AE()
        ae.add_requested_context(ModalityPerformedProcedureStep)
        
        assoc = ae.associate(dicom_host, dicom_port)
        assert assoc.is_established, f"Failed to establish DICOM association with {dicom_host}:{dicom_port}"
        
        try:
            status, ds_out = assoc.send_n_create(
                ds, ModalityPerformedProcedureStep, sop_instance_uid
            )
            assert status.Status == 0x0000, f"N-CREATE failed with status: 0x{status.Status:04X}"
        finally:
            assoc.release()

        row, stored = fetch_mpps(sop_instance_uid)
        assert row is not None, "N-CREATE did not store an MPPS row"
        assert row["status"] == "IN_PROGRESS"
        assert row["completed_at"] is None
        assert row["mwl_id"] == fetch_mwl(setup_mwl_entry["AccessionNumber"])["id"]
        assert row["AccessionNumber"] == setup_mwl_entry["AccessionNumber"]
        assert row["StudyInstanceUID"] == setup_mwl_entry["StudyInstanceUID"]
        assert row["PatientID"] == "P1"
        assert row["performed_procedure_step_id"] == "PPS001"
        assert row["performed_station_ae_title"] == "ORTHANC"
        assert stored.PerformedProcedureStepStatus == "IN PROGRESS"
        assert stored.PerformedStationAETitle == "ORTHANC"
        assert stored.Modality == "CT"
        step = stored.ScheduledStepAttributesSequence[0]
        assert step.AccessionNumber == setup_mwl_entry["AccessionNumber"]
        assert step.StudyInstanceUID == setup_mwl_entry["StudyInstanceUID"]
        assert fetch_mwl(setup_mwl_entry["AccessionNumber"])["completed"] == 0
    
    def test_mpps_nset(self, setup_mwl_entry):
        """Test MPPS N-SET operation"""
        matches = self.query_worklist(setup_mwl_entry["AccessionNumber"])
        assert [match.StudyInstanceUID for match in matches] == [setup_mwl_entry["StudyInstanceUID"]]

        sop_instance_uid = generate_uid()
        dicom_host = get_dicom_host()
        dicom_port = get_dicom_port()
        
        # N-CREATE
        ds_create = TestData.create_mpps_dataset(sop_instance_uid, setup_mwl_entry, "IN PROGRESS")
        ae = AE()
        ae.add_requested_context(ModalityPerformedProcedureStep)
        
        assoc = ae.associate(dicom_host, dicom_port)
        assert assoc.is_established
        
        try:
            status, _ = assoc.send_n_create(
                ds_create, ModalityPerformedProcedureStep, sop_instance_uid
            )
            assert status.Status == 0x0000
            print("N-CREATE for N-SET test successful!")
        finally:
            assoc.release()
        
        # N-SET to complete
        ds_set = TestData.create_mpps_dataset(sop_instance_uid, setup_mwl_entry, "COMPLETED")
        assoc = ae.associate(dicom_host, dicom_port)
        assert assoc.is_established
        
        try:
            status, _ = assoc.send_n_set(
                ds_set, ModalityPerformedProcedureStep, sop_instance_uid
            )
            assert status.Status == 0x0000, f"N-SET failed with status: 0x{status.Status:04X}"
        finally:
            assoc.release()

        row, stored = fetch_mpps(sop_instance_uid)
        assert row["status"] == "COMPLETED"
        assert row["completed_at"] is not None
        assert row["performed_procedure_step_id"] == "PPS001"
        assert row["performed_station_ae_title"] == "ORTHANC"
        assert stored.PerformedProcedureStepStatus == "COMPLETED"
        assert stored.PerformedProcedureStepID == "PPS001"
        assert stored.PerformedStationAETitle == "ORTHANC"
        assert stored.PerformedProcedureStepStartDate
        assert stored.PerformedProcedureStepEndDate
        assert stored.Modality == "CT"
        assert stored.ScheduledStepAttributesSequence[0].AccessionNumber == setup_mwl_entry["AccessionNumber"]
        assert stored.PerformedSeriesSequence[0].SeriesDescription == "Chest CT Images"
        assert fetch_mwl(setup_mwl_entry["AccessionNumber"])["completed"] == 1
        assert self.query_worklist(setup_mwl_entry["AccessionNumber"]) == []

    def test_mwl_scheduled_step_query(self, setup_mwl_entry):
        """A modality query filters inside Scheduled Procedure Step Sequence."""
        today = date.today().strftime("%Y%m%d")
        query = Dataset()
        query.PatientName = "DOE*"
        query.AccessionNumber = setup_mwl_entry["AccessionNumber"]
        query.StudyInstanceUID = ""
        step = Dataset()
        step.ScheduledStationAETitle = "ORTHANC"
        step.Modality = "OT"
        step.ScheduledProcedureStepStartDate = f"{today}-{today}"
        query.ScheduledProcedureStepSequence = [step]

        matches = self.query_worklist(query=query)
        assert len(matches) == 1
        stored_step = matches[0].ScheduledProcedureStepSequence[0]
        assert stored_step.Modality == "OT"
        assert stored_step.ScheduledProcedureStepID == "SPS1"
        assert stored_step.ScheduledProcedureStepStartTime

        step.ScheduledStationAETitle = "NOMATCH"
        assert self.query_worklist(query=query) == []

    def test_duplicate_ncreate(self, setup_mwl_entry):
        sop_instance_uid = generate_uid()
        ds = TestData.create_mpps_dataset(sop_instance_uid, setup_mwl_entry, "IN PROGRESS")
        ae = AE()
        ae.add_requested_context(ModalityPerformedProcedureStep)
        assoc = ae.associate(get_dicom_host(), get_dicom_port())
        assert assoc.is_established
        try:
            status, _ = assoc.send_n_create(ds, ModalityPerformedProcedureStep, sop_instance_uid)
            assert status.Status == 0x0000
            status, _ = assoc.send_n_create(ds, ModalityPerformedProcedureStep, sop_instance_uid)
            assert status.Status == 0x0111, f"Duplicate N-CREATE returned 0x{status.Status:04X}"
        finally:
            assoc.release()

    def test_duplicate_accession_is_rejected(self, setup_mwl_entry):
        response = requests.post(
            f"{get_api_url()}/mwl/create_from_json",
            json=setup_mwl_entry,
            timeout=10,
        )
        assert response.status_code == 409, response.text

    @staticmethod
    def query_worklist(accession_number=None, query=None):
        if query is None:
            query = Dataset()
            query.AccessionNumber = accession_number
            query.StudyInstanceUID = ""
        expected_accession = accession_number or getattr(query, "AccessionNumber", None)

        ae = AE()
        ae.add_requested_context(ModalityWorklistInformationFind)
        ae.acse_timeout = ae.dimse_timeout = ae.network_timeout = 10
        assoc = ae.associate(get_dicom_host(), get_dicom_port())
        assert assoc.is_established, "Failed to establish MWL association"

        matches = []
        statuses = []
        try:
            for status, identifier in assoc.send_c_find(query, ModalityWorklistInformationFind):
                assert "Status" in status, "No C-FIND status received"
                statuses.append(status.Status)
                if status.Status in (0xFF00, 0xFF01):
                    if expected_accession:
                        assert identifier.AccessionNumber == expected_accession
                    matches.append(identifier)
                else:
                    assert status.Status == 0x0000, f"C-FIND failed: 0x{status.Status:04X}"
        finally:
            assoc.release()

        assert statuses and statuses[-1] == 0x0000, "No final C-FIND success received"
        return matches


if __name__ == "__main__":
    pytest.main([__file__, "-v"]) 