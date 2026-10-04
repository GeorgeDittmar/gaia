"""Calculate the Fibonacci sequence up to a given count."""

import sys


def fibonacci(n: int) -> list[int]:
    """Return the first *n* Fibonacci numbers.

    Args:
        n: Number of terms to generate. Must be >= 0.

    Returns:
        List of the first *n* Fibonacci numbers starting from 0.
    """
    if n <= 0:
        return []
    if n == 1:
        return [0]

    seq: list[int] = [0, 1]
    for _ in range(2, n):
        seq.append(seq[-1] + seq[-2])
    return seq


def main() -> None:
    if len(sys.argv) > 1:
        n = int(sys.argv[1])
    else:
        n = 10  # default

    result = fibonacci(n)
    print(", ".join(str(x) for x in result))


if __name__ == "__main__":
    main()
