# Ensures the project root is on sys.path for both pytest and direct execution.


def pytest_addoption(parser):
    parser.addoption(
        "--recording",
        default=None,
        help="Path to a sequence.txt file to replay as a recording-driven test.",
    )
