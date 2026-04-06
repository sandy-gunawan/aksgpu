import torch
import torch.nn as nn
from app.config import HIDDEN_SIZE, NUM_LAYERS, DROPOUT, NUM_FEATURES, OUTPUT_WINDOW


class WeatherLSTM(nn.Module):
    """
    2-layer LSTM for time-series weather forecasting.

    Input:  (batch, INPUT_WINDOW, NUM_FEATURES)   e.g. (64, 168, 5)
    Output: (batch, OUTPUT_WINDOW, NUM_FEATURES)   e.g. (64, 24, 5)
    """

    def __init__(
        self,
        num_features: int = NUM_FEATURES,
        hidden_size: int = HIDDEN_SIZE,
        num_layers: int = NUM_LAYERS,
        dropout: float = DROPOUT,
        output_window: int = OUTPUT_WINDOW,
    ):
        super().__init__()
        self.num_features = num_features
        self.hidden_size = hidden_size
        self.output_window = output_window

        self.lstm = nn.LSTM(
            input_size=num_features,
            hidden_size=hidden_size,
            num_layers=num_layers,
            dropout=dropout if num_layers > 1 else 0.0,
            batch_first=True,
        )
        self.fc = nn.Linear(hidden_size, output_window * num_features)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x shape: (batch, seq_len, num_features)
        lstm_out, _ = self.lstm(x)
        # Take only the last time-step output
        last_hidden = lstm_out[:, -1, :]  # (batch, hidden_size)
        out = self.fc(last_hidden)  # (batch, output_window * num_features)
        out = out.view(-1, self.output_window, self.num_features)
        return out
