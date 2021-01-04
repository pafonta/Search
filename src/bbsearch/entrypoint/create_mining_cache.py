"""EntryPoint for mining a database and saving of extracted items in a cache."""
import argparse
import getpass
import logging
import pathlib
import sys

import sqlalchemy
from sqlalchemy.pool import NullPool

from ..utils import DVC
from ._helper import CombinedHelpFormatter, configure_logging, parse_args_or_environment


def run_create_mining_cache(argv=None):
    """Mine all texts in database and save results in a cache.

    Parameters
    ----------
    argv : list_like of str
        The command line arguments.
    """
    parser = argparse.ArgumentParser(
        description="Mine the CORD-19 database and cache the results.",
        formatter_class=CombinedHelpFormatter,
    )
    parser.add_argument(
        "--db-type",
        default="mysql",
        type=str,
        choices=("mysql", "sqlite"),
        help="Type of the database.",
    )
    parser.add_argument(
        "--database-url",
        type=str,
        help="""
        The location of the database depending on the database type.

        For MySQL the server URL should be provided, for SQLite the
        location of the database file. Generally, the scheme part of
        the URL should be omitted, e.g. for MySQL the URL should be
        of the form 'my_sql_server.ch:1234/my_database' and for SQLite
        of the form '/path/to/the/local/database.db'.

        If missing, then the environment variable DATABASE_URL will
        be read.
        """,
        default=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--target-table-name",
        default="mining_cache_temporary",
        type=str,
        help="The name of the target mining cache table",
    )
    parser.add_argument(
        "--n-processes-per-model",
        default=1,
        type=int,
        help="""
        Each mining model is run in parallel with respect to the others.
        In addition to that, n-processes-per-model are used to run in
        parallel a single mining model.
        """,
    )
    parser.add_argument(
        "--restrict-to-models",
        type=str,
        default=None,
        help="""
        Comma-separated list of models (as called in ee_models_library_file)
        to be run to populate the cache. By default, all models in
        ee_models_library_file are run.
        """,
    )
    parser.add_argument(
        "--log-file",
        "-l",
        type=str,
        metavar="<filename>",
        default=None,
        help="In addition to stderr, log messages to a file.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="count",
        default=0,
        help="The logging level, -v correspond to INFO, -vv to DEBUG",
    )

    # Parse CLI arguments
    env_variable_names = {
        "database_url": "DATABASE_URL",
    }
    args = parse_args_or_environment(parser, env_variable_names, argv=argv)

    # Configure logging
    if args["verbose"] == 1:
        level = logging.INFO
    elif args["verbose"] >= 2:
        level = logging.DEBUG
    else:
        level = logging.WARNING
    configure_logging(args["log_file"], level)

    logger = logging.getLogger("Mining cache entrypoint")
    logger.info("Welcome to the mining cache creation")
    logger.info("Parameters:")
    logger.info(f"db-type                : {args['db_type']}")
    logger.info(f"database-url           : {args['database_url']}")
    logger.info(f"target-table-name      : {args['target_table_name']}")
    logger.info(f"n-processes-per-model  : {args['n_processes_per_model']}")
    logger.info(f"restrict-to-models     : {args['restrict_to_models']}")
    logger.info(f"log-file               : {args['log_file']}")
    logger.info(f"verbose                : {args['verbose']}")

    # Loading libraries
    logger.info("Loading libraries")
    from ..database import CreateMiningCache

    # Database type
    logger.info("Parsing the database type")
    if args["db_type"] == "sqlite":
        database_path = pathlib.Path(args["database_url"])
        if not database_path.exists():
            raise FileNotFoundError(f"No database found at {database_path}.")
        database_url = f"sqlite:///{database_path}"
    elif args["db_type"] == "mysql":
        password = getpass.getpass("MySQL root password: ")
        database_url = f"mysql+pymysql://root:{password}@{args['database_url']}"
    else:  # pragma: no cover
        # Will never get here because `parser.parse_args()` will fail first.
        # This is because we have choices=("mysql", "sqlite") in the
        # argparse parameters
        raise ValueError("Invalid database type specified under --db-type")

    # Create the database engine
    logger.info("Creating the database engine")
    # The NullPool prevents the Engine from using any connection more than once
    # This is important for multiprocessing
    database_engine = sqlalchemy.create_engine(database_url, poolclass=NullPool)

    # Load the models library
    logger.info("Loading the models library")
    ee_models_library = DVC.load_ee_models_library()

    # Restrict to given models
    if args["restrict_to_models"] is not None:
        logger.info("Restricting to a subset of models")
        model_selection = args["restrict_to_models"].split(",")
        model_selection = set(map(lambda s: s.strip(), model_selection))
        for model_path in model_selection:
            if model_path not in ee_models_library["model"].values:
                logger.warning(
                    f"Can't restrict to model {model_path} because it is not "
                    f"listed in the models library file. This entry will be ignored."
                )
        keep_rows = ee_models_library["model"].isin(model_selection)
        ee_models_library = ee_models_library[keep_rows]

    # Create the cache creation class and run the cache creation
    logger.info("Creating the cache miner")
    cache_creator = CreateMiningCache(
        database_engine=database_engine,
        ee_models_library=ee_models_library,
        target_table_name=args["target_table_name"],
        workers_per_model=args["n_processes_per_model"],
    )

    logger.info("Launching the mining")
    cache_creator.construct()

    logger.info("All done, bye")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(run_create_mining_cache())
