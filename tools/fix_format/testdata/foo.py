"""Example Python module level docstring.

The Foo class has two methods, hello and hello_name.

For more advanced examples of docstrings, see:

    https://sphinxcontrib-napoleon.readthedocs.io/en/latest/example_google.html
"""


class Foo:
    """Summary line for Foo class, should fit on one line.

    Additional documentation stanzas can be provided after the summary line.
    Class attributes should be listed here as well.
    """

    def hello(self) -> str:
        """Example class method with no arguments.

        Returns:
            str, 'Hello World'

        >>> foo.hello()
        'Hello World!'
        """
        return "Hello World!"

    def hello_name(self, name: str) -> str:
        """Example class method with PEP484 type annotation.

        Args:
          name (str): A name to say hello to

        Returns:
            str, A hello message for name.

        >>> foo.hello_name('Enfabricator')
        'Hello Enfabricator!'
        """
        return f"Hello {name}!"
