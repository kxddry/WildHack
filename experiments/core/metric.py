import numpy as np


class WapePlusRbias:
    """WAPE + |Relative Bias| metric for the WildHack competition."""

    @property
    def name(self) -> str:
        return "wape_plus_rbias"

    def calculate(self, y_true: np.ndarray, y_pred: np.ndarray) -> float:
        wape = (np.abs(y_pred - y_true)).sum() / y_true.sum()
        rbias = np.abs(y_pred.sum() / y_true.sum() - 1)
        return wape + rbias
