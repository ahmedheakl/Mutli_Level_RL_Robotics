"""
Implementation of features exctractors for both the teacher and the robot
"""
from typing import Optional
from gym import spaces
import torch.nn as nn
import torch as th
from stable_baselines3.common.torch_layers import BaseFeaturesExtractor


class LSTMFeatureExtractor(BaseFeaturesExtractor):
    """Input"""

    def __init__(self, observation_space: spaces.Box, features_dim: int = 6):  # type: ignore
        super().__init__(observation_space, features_dim)
        self.LSTM = nn.LSTM(input_size=features_dim, hidden_size=16, num_layers=1)

    def forward(self, observations: th.Tensor) -> th.Tensor:
        # th.tensor(observations)
        observations.clone().detach()
        self.LSTM_output, self.LSTM_hidden = self.LSTM(observations)
        return self.LSTM_output + self.LSTM_hidden[0] + self.LSTM_hidden[1]


class TeacherFeatureExtractor(BaseFeaturesExtractor):
    """Feature extractor for the teacher implemented using LSTM.

    The model takes in for each step:
        (1) Number of sucesses for the last robot session
        (2) Robot average reward for the last robot session
        (3) Average number of steps per episode for the last robot session
        (4) Robot level of the upcoming session

    and produces the difficulty for the upcoming session.
    """

    # pylint: disable=too-many-arguments
    def __init__(
        self,
        observation_space: spaces.Box,
        features_dim: int = 4,
        hidden_size: int = 8,
        num_layers: int = 1,
        device: str = "cuda",
        batch_size: int = 1,
    ) -> None:
        super().__init__(observation_space, features_dim)
        self.lstm = nn.LSTM(
            input_size=features_dim,
            hidden_size=hidden_size,
            num_layers=num_layers,
        )
        self.hidden_size = hidden_size
        self.device = device
        self.batch_size = batch_size
        self.num_layers = num_layers
        self._init_hidden()

    def _init_hidden(self) -> None:
        """Initialized hidden tensor. This method should be called whenever
        the level of the robot resets to 0."""

        # Dim = [num_layers, batch_size, hidden_size]
        self.hidden = th.zeros(
            self.num_layers,
            self.batch_size,
            self.hidden_size,
            device=self.device,
        )

        # Dim = [num_layers, batch_size, hidden_size]
        self.cell = th.zeros(
            self.num_layers,
            self.batch_size,
            self.hidden_size,
            device=self.device,
        )

    def forward(self, observations: th.Tensor) -> th.Tensor:
        """Forward pass through the feature extractor"""

        # Re-initialize hidden state if the robot level is reset
        if observations[3] < 2:
            self._init_hidden()

        # Dim = [seq_len, batch_size, input_size]
        # Here is seq_len=1, since we generating a new environment
        # for every single observation.
        observations = observations.to(self.device)
        output_tensor, (self.hidden, self.cell) = self.lstm(
            observations,
            (self.hidden, self.cell_gate),
        )
        return output_tensor


class Robot2DFeatureExtractor(BaseFeaturesExtractor):
    def __init__(self, observation_space: spaces.Dict, features_dim: int = 37):
        super().__init__(observation_space=observation_space, features_dim=features_dim)

        n_input_channels = 1
        self.cnn = nn.Sequential(
            nn.Conv2d(n_input_channels, 32, kernel_size=8, stride=4),
            nn.ReLU(),
            nn.Conv2d(32, 64, kernel_size=6, stride=4),
            nn.ReLU(),
            nn.Conv2d(64, 128, kernel_size=3, stride=4),
            nn.ReLU(),
            nn.Conv2d(128, 256, kernel_size=1, stride=4),
            nn.ReLU(),
            nn.Flatten(),
            nn.Linear(256, 32),
        )

    def forward(self, observations: th.Tensor) -> th.Tensor:
        lidar_obs = observations["lidar"]  # type: ignore
        rs_obs = observations["robot"]  # type: ignore
        lidar_obs = th.unsqueeze(lidar_obs, dim=1)
        return th.cat((self.cnn(lidar_obs), rs_obs), axis=1)  # type: ignore


class Robot1DFeatureExtractor(BaseFeaturesExtractor):
    def __init__(self, observation_space: spaces.Dict, features_dim: int = 37):
        super().__init__(observation_space=observation_space, features_dim=features_dim)

        n_input_channels = 1
        self.cnn = nn.Sequential(
            nn.Conv1d(n_input_channels, 32, kernel_size=8, stride=4),
            nn.ReLU(),
            nn.Conv1d(32, 64, kernel_size=6, stride=4),
            nn.ReLU(),
            nn.Conv1d(64, 128, kernel_size=3, stride=4),
            nn.ReLU(),
            nn.Conv1d(128, 256, kernel_size=1, stride=4),
            nn.ReLU(),
            nn.Flatten(),
            nn.Linear(1024, 32),
        )

    def forward(self, observations: th.Tensor) -> th.Tensor:
        lidar_obs = observations["lidar"]  # type: ignore
        rs_obs = observations["robot"]  # type: ignore
        lidar_obs = th.unsqueeze(lidar_obs, dim=1)
        return th.cat((self.cnn(lidar_obs), rs_obs), axis=1)  # type: ignore


class CustomCombinedExtractor(BaseFeaturesExtractor):
    def __init__(self, observation_space: spaces.Dict):
        # We do not know features-dim here before going over all the items,
        # so put something dummy for now. PyTorch requires calling
        # nn.Module.__init__ before adding modules
        super(CustomCombinedExtractor, self).__init__(observation_space, features_dim=1)

        extractors = {}

        total_concat_size = 0
        chosen_model: Optional[nn.Module] = None
        # We need to know size of the output of this extractor,
        # so go over all the spaces and compute output feature sizes
        for key, subspace in observation_space.spaces.items():  # type: ignore
            if key == "image":
                # We will just downsample one channel of the image by 4x4 and flatten.
                # Assume the image is single-channel (subspace.shape[0] == 0)
                extractors[key] = nn.Sequential(nn.MaxPool2d(4), nn.Flatten())
                total_concat_size += subspace.shape[1] // 4 * subspace.shape[2] // 4
            elif key == "vector":
                # Run through a simple MLP
                extractors[key] = extractors[key] = nn.Linear(subspace.shape[0], 16)
                total_concat_size += 16

        self.extractors = nn.ModuleDict(extractors)

        # Update the features dim manually
        self._features_dim = total_concat_size

    def forward(self, observations) -> th.Tensor:
        encoded_tensor_list = []

        # self.extractors contain nn.Modules that do all the processing.
        for key, extractor in self.extractors.items():
            encoded_tensor_list.append(extractor(observations[key]))  # type: ignore
        # Return a (B, self._features_dim) PyTorch tensor, where B is batch dimension.
        return th.cat(encoded_tensor_list, dim=1)
