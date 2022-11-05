import torch
import torch.nn as nn
from stable_baselines3 import PPO
from stable_baselines3.common.torch_layers import BaseFeaturesExtractor
from stable_baselines3.common.policies import ActorCriticPolicy

# Custom_Feature_Extractor


class CustomLSTM(BaseFeaturesExtractor):
    # observation_space is the input_dim of the feature extractor
    # feature dim is the output_dim of the feature extractor
    # n_layers are the number of layers of the LSTM in the feature extractor

    def __init__(self, observation_space, num_layers,  features_dim):
        super(CustomLSTM, self).__init__(observation_space, features_dim)

        input_size = observation_space.shape[0]

        self.lstm = nn.Sequential(
            nn.LSTM(input_size=input_size,
                    hidden_size=features_dim, num_layers=num_layers)
        )

    # observations is the tuple or list representing the current observation

    def forward(self, observations):
        features = self.lstm(observations)
        # print(len(features[0][0]))
        return torch.tensor(features[0])

    # here we input our arguements (features_dim , num_layers_lstm)


policy_kwargs = dict(
    features_extractor_class=CustomLSTM,
    features_extractor_kwargs=dict(features_dim=128, num_layers=3),
)


# Custom_Policy/ Value_function

class CustomNetwork(nn.Module):
    """
    Custom network for policy and value function.
    It receives as input the features extracted by the feature extractor.

    :param feature_dim: dimension of the features extracted with the features_extractor (e.g. features from a CNN)
    :param last_layer_dim_pi: (int) number of units for the last layer of the policy network
    :param last_layer_dim_vf: (int) number of units for the last layer of the value network
    """

    def __init__(
        self,
        feature_dim,
        last_layer_dim_pi=64,
        last_layer_dim_vf=64,
    ):
        super(CustomNetwork, self).__init__()

        # IMPORTANT:
        # Save output dimensions, used to create the distributions
        self.latent_dim_pi = last_layer_dim_pi
        self.latent_dim_vf = last_layer_dim_vf

        # Policy network
        self.policy_net = nn.Sequential(
            nn.Linear(feature_dim, last_layer_dim_pi), nn.ReLU()
        )
        # Value network
        self.value_net = nn.Sequential(
            nn.Linear(feature_dim, last_layer_dim_vf), nn.ReLU()
        )

    def forward(self, features):
        """
        :return: (th.Tensor, th.Tensor) latent_policy, latent_value of the specified network.
            If all layers are shared, then ``latent_policy == latent_value``
        """
        return self.policy_net(features), self.value_net(features)

    def forward_actor(self, features):
        return self.policy_net(features)

    def forward_critic(self, features):
        return self.value_net(features)


class CustomActorCriticPolicy(ActorCriticPolicy):
    def __init__(
        self,
        observation_space,
        action_space,
        lr_schedule,
        net_arch=[128, dict(vf=[256], pi=[16])],
        activation_fn=nn.Tanh,
        *args,
        **kwargs,
    ):

        super(CustomActorCriticPolicy, self).__init__(
            observation_space,
            action_space,
            lr_schedule,
            net_arch,
            activation_fn,
            # Pass remaining arguments to base class
            *args,
            **kwargs,
        )
        # Disable orthogonal initialization
        self.ortho_init = False

    def _build_mlp_extractor(self):
        self.mlp_extractor = CustomNetwork(self.features_dim)
