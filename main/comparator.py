'''
Capability: Compare

Check the values and surface any mismatched fields, showing the SI and
BL values side by side.
'''

from extractor import Extractor


class Comparator:
    def __init__(self, dataExtractor: Extractor) -> None:
        self.extractor = dataExtractor

    def compare(self):
        pass