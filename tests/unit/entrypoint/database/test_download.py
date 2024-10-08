# Blue Brain Search is a text mining toolbox focused on scientific use cases.
#
# Copyright (C) 2020  Blue Brain Project, EPFL.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
import argparse
import datetime
import inspect
import logging
import pathlib
from collections import namedtuple
from itertools import chain
from unittest.mock import Mock

import pytest
from google.cloud.storage import Blob

from bluesearch.entrypoint.database import download

DOWNLOAD_PARAMS = {"source", "from_month", "output_dir", "dry_run"}


def test_init_parser():
    parser = download.init_parser(argparse.ArgumentParser())

    args = parser.parse_args(["pmc", "2020-10", "/path/to/download"])
    assert vars(args).keys() == DOWNLOAD_PARAMS

    # Test the values
    assert args.source == "pmc"
    assert args.from_month == datetime.datetime(2020, 10, 1)
    assert args.output_dir == pathlib.Path("/path/to/download")
    assert not args.dry_run

    # Invalid date
    parser = download.init_parser(argparse.ArgumentParser())
    with pytest.raises(SystemExit):
        _ = parser.parse_args(["pmc", "invalid-date", "/path/to/download"])


def test_run_arguments():
    assert inspect.signature(download.run).parameters.keys() == DOWNLOAD_PARAMS


def test_pmc_download(capsys, monkeypatch, tmp_path):
    def fake_download_articles_func(url_list, output_dir):
        for url in url_list:
            path = output_dir / url
            path.touch()

    fake_download_articles = Mock(side_effect=fake_download_articles_func)

    monkeypatch.setattr(
        "bluesearch.database.download.download_articles",
        fake_download_articles,
    )

    fake_generate_pmc_urls = Mock(return_value=["fake1", "fake2"])
    monkeypatch.setattr(
        "bluesearch.database.download.generate_pmc_urls", fake_generate_pmc_urls
    )

    fake_datetime = datetime.datetime(2021, 12, 2)
    pmc_path = tmp_path / "pmc"
    download.run("pmc", fake_datetime, pmc_path, dry_run=False)
    assert pmc_path.exists()
    assert {path.name for path in pmc_path.iterdir()} == {
        "author_manuscript",
        "oa_comm",
        "oa_noncomm",
    }
    for sub_dir in pmc_path.iterdir():
        assert len(list(sub_dir.iterdir())) == 2
    assert fake_download_articles.call_count == 3
    assert fake_generate_pmc_urls.call_count == 3

    fake_generate_pmc_urls.reset_mock()
    fake_download_articles.reset_mock()
    download.run("pmc", fake_datetime, pmc_path, dry_run=True)
    captured = capsys.readouterr()
    assert len(captured.out.split("\n")) == 10
    assert fake_generate_pmc_urls.call_count == 3
    assert fake_download_articles.call_count == 0


def test_pubmed_download(capsys, monkeypatch, tmp_path):
    def fake_download_articles_func(url_list, output_dir):
        for url in url_list:
            path = output_dir / url
            path.touch()

    # Preparations
    fake_download_articles = Mock(side_effect=fake_download_articles_func)

    monkeypatch.setattr(
        "bluesearch.database.download.download_articles",
        fake_download_articles,
    )

    fake_get_pubmed_urls = Mock(return_value=["fake1", "fake2"])
    monkeypatch.setattr(
        "bluesearch.database.download.get_pubmed_urls", fake_get_pubmed_urls
    )

    fake_datetime = datetime.datetime(2021, 12, 16)
    pubmed_path = tmp_path / "pubmed"

    # Run the command
    download.run("pubmed", fake_datetime, pubmed_path, dry_run=False)

    # Asserts
    assert pubmed_path.exists()
    assert len(list(pubmed_path.iterdir())) == 2

    assert fake_download_articles.call_count == 1
    assert fake_get_pubmed_urls.call_count == 1

    fake_get_pubmed_urls.reset_mock()
    fake_download_articles.reset_mock()

    # Run the command (dry run)
    download.run("pubmed", fake_datetime, pubmed_path, dry_run=True)

    # Asserts
    captured = capsys.readouterr()
    assert len(captured.out.split("\n")) == 1 + 2 + 1
    assert fake_get_pubmed_urls.call_count == 1
    assert fake_download_articles.call_count == 0


@pytest.mark.parametrize("source", ["biorxiv", "medrxiv"])
def test_biorxiv_medrxiv_download(source, monkeypatch, tmp_path, capsys):
    def fake_download_s3_articles_func(bucket, url_dict, output_dir):
        for url_list in url_dict.values():
            for url in url_list:
                output_path = output_dir / url
                output_path.touch()

    # Define mocks
    fake_getpass = Mock()
    fake_getpass.getpass.return_value = "somecredentials"
    fake_datetime = datetime.datetime(2021, 11, 1)

    fake_get_s3_urls = Mock(
        return_value={
            "November_2018": ["1.meca"],
            "December_2018": ["2.meca"],
        }
    )

    fake_download_s3_articles = Mock(side_effect=fake_download_s3_articles_func)

    # Patch
    monkeypatch.setattr(
        "bluesearch.entrypoint.database.download.getpass",
        fake_getpass,
    )
    monkeypatch.setattr(
        "bluesearch.database.download.get_s3_urls",
        fake_get_s3_urls,
    )
    monkeypatch.setattr(
        "bluesearch.database.download.download_s3_articles",
        fake_download_s3_articles,
    )

    # Run the command (no dry run)
    download.run(source, fake_datetime, tmp_path, dry_run=False)

    # Asserts
    fake_get_s3_urls.assert_called_once()
    fake_download_s3_articles.assert_called_once()
    assert fake_getpass.getpass.call_count == 2

    assert len(list(tmp_path.iterdir())) == 2

    # Reset mocks
    fake_get_s3_urls.reset_mock()
    fake_download_s3_articles.reset_mock()
    fake_getpass.reset_mock()

    # Run the command dry run
    download.run(source, fake_datetime, tmp_path, dry_run=True)

    # Asserts
    fake_get_s3_urls.assert_called_once()
    fake_download_s3_articles.assert_not_called()
    assert fake_getpass.getpass.call_count == 2

    stdout = capsys.readouterr().out
    assert "Month: November_2018\n1.meca\nMonth: December_2018\n2.meca\n" in stdout


class TestArxivDownload:
    @pytest.fixture
    def mocked(self, monkeypatch):
        """Set up mocks for all subsequent tests.

        Need to mock:
        * The Google Cloud Storage client and bucket
        * get_gcs_urls
        * download_gcs_blob

        For a more convenient access pack all mocks into a namedtuple and yield.
        """
        client = Mock()
        bucket = Mock()
        get_gcs_urls = Mock()
        get_gcs_urls.return_value = {
            "2111": [Blob("blob_{i}", bucket) for i in range(101)],
            "2112": [Blob("blob_x1", bucket), Blob("blob_x2", bucket)],
        }
        download_gcs_blob = Mock()
        monkeypatch.setattr("google.cloud.storage.Client", client)
        monkeypatch.setattr(
            "bluesearch.database.download.download_gcs_blob",
            download_gcs_blob,
        )
        monkeypatch.setattr(
            "bluesearch.database.download.get_gcs_urls",
            get_gcs_urls,
        )

        Mocked = namedtuple("Mocked", ["client", "get_gcs_urls", "download_gcs_blob"])
        yield Mocked(client, get_gcs_urls, download_gcs_blob)

    def test_dry_run(self, capsys, tmp_path, mocked):
        # The dry run should print all months and blobs to stdout
        download.run("arxiv", datetime.datetime(2021, 12, 1), tmp_path, dry_run=True)
        stdout, stderr = capsys.readouterr()
        for month, blobs in mocked.get_gcs_urls().items():
            assert month in stdout
            for blob in blobs:
                assert blob.name in stdout

    def test_normal_run(self, tmp_path, mocked):
        # Under normal circumstances download_gcs_blob should be called as
        # many times as there are blobs to download
        download.run("arxiv", datetime.datetime(2021, 12, 1), tmp_path, dry_run=False)
        n_blobs = sum(len(blobs) for blobs in mocked.get_gcs_urls().values())
        assert mocked.download_gcs_blob.call_count == n_blobs

    def test_worker_errors_are_reported(self, caplog, tmp_path, mocked):
        # If a worker (download_gcs_blob) raises an error then it should
        # appear in the error logs
        error_msg = "something went wrong"
        mocked.download_gcs_blob.side_effect = RuntimeError(error_msg)
        with caplog.at_level(logging.ERROR):
            download.run(
                "arxiv", datetime.datetime(2021, 12, 1), tmp_path, dry_run=False
            )
        assert error_msg in caplog.text
        for blob in chain(*mocked.get_gcs_urls().values()):
            assert f"{blob.name} failed" in caplog.text


@pytest.mark.parametrize(
    ("source", "expected_date"),
    [
        ("arxiv", "April 2007"),
        ("biorxiv", "December 2018"),
        ("medrxiv", "October 2020"),
        ("pmc", "December 2021"),
        ("pubmed", "December 2021"),
    ],
)
def test_structure_change(source, expected_date, tmp_path, caplog):

    limit_datetime = download.MIN_DATE[source]
    fake_datetime = limit_datetime - datetime.timedelta(days=32)

    with caplog.at_level(logging.ERROR):
        exit_code = download.run(source, fake_datetime, tmp_path, dry_run=False)

    assert exit_code == 1
    assert expected_date in caplog.text
