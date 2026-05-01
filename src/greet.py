from datetime import datetime


def greet(name: str) -> None:
    """
    Prints a personalized greeting message to the console along with the current time.

    Args:
        name (str): The name of the person to greet.

    Example:
        >>> greet("Alice")
        Hello, Alice!
        Current time: 2023-10-27 10:00:00
    """
    print(f"Hello, {name}!")
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"Current time: {current_time}")


if __name__ == "__main__":
    # Example usage
    greet("World")