"""Tiny stand-ins for the Earth Engine objects the steps touch. No network, no `ee.Initialize`."""


class FakeValue:
    """Stands in for a server-side value: only `.getInfo()` is used."""

    def __init__(self, value):
        self.value = value

    def getInfo(self):
        return self.value


class FakeImage:
    def __init__(self, name="image"):
        self.name = name

    def __repr__(self):
        return f"FakeImage({self.name!r})"


class FakeCollection:
    """Stands in for ee.ImageCollection: `.size().getInfo()` and `.first()`."""

    def __init__(self, images):
        self.images = list(images)

    def size(self):
        return FakeValue(len(self.images))

    def first(self):
        return self.images[0] if self.images else None
