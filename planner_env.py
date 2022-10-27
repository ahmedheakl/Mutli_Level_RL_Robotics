from typing import List
from envs.env import TestEnv
from gym import Env, spaces
from obstacle.obstacles import Obstacles
from obstacle.singleobstacle import SingleObstacle
import numpy as np
from numpy.linalg import norm
from utils.calculations import *
from policy.custom_policy import CustomFeatureExtractor
from stable_baselines3 import PPO
from random import randint, random, uniform
from planner.planner import CustomActorCriticPolicy
from planner.planner import CustomLSTM
from utils.planner_checker import PlannerChecker
from utils.callback import RobotCallback
from utils.robot import Robot

class PlannerEnv(Env):
    def __init__(self) -> None:
        super(PlannerEnv, self).__init__()
        self.action_space_names = ["P_robot", "P_goal", "d_no.obstacles"]
        self.action_space = spaces.Box(
            low=1, high=7, shape=(3,), dtype=np.float32)
        self.observation_space = spaces.Box(low=-1000, high=1000,
                                shape=(3,), dtype=np.float32)
        self.env = TestEnv()
        self.episodes = 0
        self.current_difficulty = 0
        self.desired_difficulty = 0
        self.time_steps = 0


        self.robot_level = 0


        self.diff_checker = PlannerChecker()

        # define constants 
        self.GAMMA_DIFFICULITY = 10
        self.GAMMA_REWARD = 10
        self.GAMMA_EPISODE = 10
        self.ADVANCE_PROBABILITY = 0.9

        

    def _make_obs(self):
        # TODO: write docstring

        # time_steps, robot_level, robot_reward, difficulty
        print([self.robot_level, self.env.total_reward, self.current_difficulty])
        return [self.robot_level, self.env.total_reward, self.current_difficulty]

    def _get_action(self, action):
        """Convert action form list format to dict format

        Args:
            action (list): output of planner model

        Returns:
            dict: action dictionay (robotPosition, goalPosition, numberOfObstacles)
        """
        #TODO: Find out why is the models stop being saved at Agent_Model_416
        planner_output = {}
        for i in range(len(action)):
            planner_output["{}".format(self.action_space_names[i])] = action[i]
        return planner_output

    

    def step(self, action):
        
        action = self._get_action(action)
        self.env.planner_output = action
        reward = - (self.GAMMA_DIFFICULITY / (self.current_difficulty + 1.0)
                             + self.GAMMA_REWARD * (self.env.reward)/3600.0
                             + self.GAMMA_EPISODE * (self.current_difficulty >= self.desired_difficulty)) # 3600 is the max reward

        self.env.robot.set(px=100*action["P_robot"], py=150*action["P_robot"], gx=100*action["P_goal"], gy=400*action["P_goal"],
                        gt=0, vx=0, vy=0, w=0, theta=0, radius=20)

        self.env.obstacles = Obstacles()
        for i in range(int(action["d_no.obstacles"])):
            #TODO: check how openGL renders dims
            nooverlap = False
            new_obstacle = None
            while not nooverlap:
                px = randint(0, self.env.width)
                py = randint(0,self.env.height)
                new_width = randint(50,500)
                new_height = randint(50,500)
                new_obstacle = SingleObstacle(px, py, new_width, new_height)
                nooverlap = self._check_overlap(new_obstacle)
            self.env.obstacles.obstacles_list.append(new_obstacle)
                
        
        
        
        args_list = [self.env.robot.px, self.env.robot.py, self.env.robot.gx, self.env.robot.gy]
        args_list = list(map(int, args_list))
        self.current_difficulty = self.diff_checker.get_map_difficulity(self.env.obstacles, self.env.height, self.env.width, *args_list)

        self.desired_difficulty = self.env.init_difficulty * (1.15)**(self.episodes)


        # flag to advance to next level
        advance_flag = uniform(0, 1) <= self.ADVANCE_PROBABILITY
        self.robot_level = (self.robot_level + advance_flag) * advance_flag


        # start training the robot   
        policy_kwargs = dict(features_extractor_class=CustomFeatureExtractor)
        
        if self.time_steps == 0 or not advance_flag:
            model = PPO("MultiInputPolicy", self.env,
                    policy_kwargs=policy_kwargs, verbose=2)
        else:
            model = PPO.load("agent_models/Agent_Model_{}".format(self.episodes), self.env)
            
        
        model.learn(total_timesteps=1e9,  callback = RobotCallback(verbose=0, max_steps=1e3))
        model.save("agent_models/Agent_Model_{}".format(self.episodes))

        if self.current_difficulty >= self.desired_difficulty:
            done = True
            self.episodes += 1

        self.time_steps += 1
        self.env.reset()
        
        return self._make_obs(), reward, done, {"Teacher_episode_number": self.episodes}
    
    def _check_overlap(self, obstacle: SingleObstacle, robot: Robot=None):
        """Check if there is no overlap between the robot and an obstacle

        Args:
            obstacle (SingleObstacle): input obstalce
            robot (Robot, optional): input robot. Defaults to None.

        Returns:
            bool: flag to determine if there is no overlap
        """
        if robot == None:
            robot = self.env.robot
        # min_x, min_y, max_x, max_y
        dummy1 = [[robot.px - robot.radius, robot.py - robot.radius,
                 robot.px + robot.radius, robot.py + robot.radius],
                 [obstacle.px, obstacle.py, obstacle.px + obstacle.width, obstacle.py + obstacle.height]]
        dummy2 = [[robot.gx - robot.radius, robot.gy - robot.radius,
                 robot.gx + robot.radius, robot.gy + robot.radius],
                 [obstacle.px, obstacle.py, obstacle.px + obstacle.width, obstacle.py + obstacle.height]]
        return self._overlap_handler(dummy1) and self._overlap_handler(dummy2)

    def _overlap_handler(self, dummy):
        for i in range(2):
            if dummy[0][0] > dummy[1][2] or dummy[0][1] > dummy[1][3]:
                return True
            dummy[0], dummy[1] = dummy[1], dummy[0]

        return False
    
    def reset(self):
        self.time_steps = 0
        return self._make_obs()



if __name__ == '__main__':
    planner_env = PlannerEnv()
    policy_kwargs = dict(
    features_extractor_class=CustomLSTM,
    features_extractor_kwargs=dict(features_dim=2),
)
    model = PPO(CustomActorCriticPolicy, planner_env , verbose=1)
    planner_iter = 3
    for i in range(planner_iter):
        model.learn(total_timesteps=1)
        model.save("planner_models/Planner_Model_{}".format(i))
    # env = TestEnv()
    # policy_kwargs = dict(features_extractor_class=CustomFeatureExtractor)
    # model = PPO.load("Agent_Models/Agent_Model_{}".format(416), env)
    # model.learn(total_timesteps=10)