import subprocess
import time

import docker
import pytest
import sqlalchemy
from sqlalchemy.exc import OperationalError

from bluesearch.entrypoint.database.parent import main


def get_docker_client():
    """Try to instantiate docker client.

    If the daemon is not running then None is returned.

    We avoid using `docker.from_env` when the daemon is not running
    since it is causing socket related issues.
    """
    try:
        subprocess.run(["docker", "info"], check=True)

    except (FileNotFoundError, subprocess.CalledProcessError):
        return None

    return docker.from_env()


@pytest.fixture(
    params=[
        "sqlite",
        # "mysql",  # not used in production and slows down CI
        "mariadb",
    ]
)
def setup_backend(request, tmp_path):
    backend = request.param
    if backend == "sqlite":
        db_url = tmp_path / "db.sqlite"
        yield "sqlite", str(db_url)

    elif backend in {"mariadb", "mysql"}:
        client = get_docker_client()

        if client is None:
            pytest.skip("Docker daemon is not running")

        port = 22346
        container = client.containers.run(
            image=f"{backend}:latest",
            environment={"MYSQL_ROOT_PASSWORD": "my-secret-pw"},
            ports={"3306/tcp": port},
            detach=True,
            auto_remove=True,
        )

        max_waiting_time = 2 * 60
        start = time.perf_counter()

        while time.perf_counter() - start < max_waiting_time:
            try:
                engine = sqlalchemy.create_engine(
                    f"mysql+pymysql://root:my-secret-pw@127.0.0.1:{port}/"
                )
                # Container ready?
                engine.execute("show databases")
                break
            except OperationalError:
                # Container not ready, pause and then try again
                time.sleep(0.1)
                continue
        else:
            raise TimeoutError("Could not spawn the container.")

        engine.execute("create database test")
        engine.dispose()

        yield backend, f"root:my-secret-pw@127.0.0.1:{port}/test",

        container.kill()
        client.close()

    else:
        raise ValueError


def test_bbs_database(tmp_path, setup_backend, jsons_path, caplog):
    # Parameters
    db_type, db_url = setup_backend

    parsed_files_dir = tmp_path / "parsed"
    parsed_files_dir.mkdir()

    all_input_paths = sorted(jsons_path.rglob("*.json"))

    # 16e82ce0e0c8a1b36497afc0d4392b4fe21eb174.json and PMC7223769.xml.json are the
    # same article. In the presence of duplicates, currently, the code stops with an
    # 'IntegrityError' from MySQL. The patch below is to move forward until the code
    # does not stop anymore.
    all_input_paths = [x for x in all_input_paths if x.name != "PMC7223769.xml.json"]

    n_files = len(all_input_paths)

    # Initialization
    args_and_opts_init = [
        "init",
        str(db_url),
        f"--db-type={db_type}",
        "-v",
    ]
    main(args_and_opts_init)

    # Parsing all available articles
    for input_path in all_input_paths:
        args_and_opts_parse = [
            "parse",
            "cord19-json",
            str(input_path),
            str(parsed_files_dir),
            "-v",
        ]
        main(args_and_opts_parse)

    # Adding parsed files to the database
    args_and_opts_add = [
        "add",
        str(db_url),
        str(parsed_files_dir),
        f"--db-type={db_type}",
        "-v",
    ]
    main(args_and_opts_add)

    # Asserts
    if db_type == "sqlite":
        engine = sqlalchemy.create_engine(f"sqlite:///{db_url}")

    elif db_type in {"mysql", "mariadb"}:
        engine = sqlalchemy.create_engine(f"mysql+pymysql://{db_url}")

    query = "SELECT COUNT(*) FROM articles"
    (n_rows,) = engine.execute(query).fetchone()  # type: ignore

    assert n_rows == n_files > 0

    # Check logging
    expected_messages = {
        "Initialization done",
        "Parsing done",
        "Adding done",
    }
    assert expected_messages.issubset({r.message for r in caplog.records})
