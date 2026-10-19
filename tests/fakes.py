"""Tiny stand-ins for the Earth Engine objects the steps touch. No network, no `ee.Initialize`."""


class FakeValue:
    """Stands in for a server-side value: only `.getInfo()` is used."""

    def __init__(self, value):
        self.value = value

    def getInfo(self):
        return self.value


class FakeImage:
    def __init__(self, name="image", properties=None):
        self.name = name
        self.properties = properties or {}

    def __repr__(self):
        return f"FakeImage({self.name!r})"


class FakeCollection:
    """Stands in for ee.ImageCollection: `.size().getInfo()`, `.sort(...)` and `.first()`."""

    def __init__(self, images):
        self.images = list(images)
        self.sort_calls = []

    def size(self):
        return FakeValue(len(self.images))

    def sort(self, property_name, ascending=True):
        self.sort_calls.append((property_name, ascending))
        ordered = sorted(
            self.images,
            key=lambda image: image.properties.get(property_name, 0),
            reverse=not ascending,
        )
        return FakeCollection(ordered)

    def first(self):
        return self.images[0] if self.images else None
