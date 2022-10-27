
from typing import Any, List
from gym import Env, spaces
from utils.action import *
from obstacle.obstacles import Obstacles
from obstacle.singleobstacle import SingleObstacle
import numpy as np
from numpy.linalg import norm
from CMap2D import flatten_contours, render_contours_in_lidar, CMap2D, CSimAgent, fast_2f_norm
from pose2d import apply_tf_to_vel, inverse_pose2d, apply_tf_to_pose
from utils.calculations import *
import threading
from policy.custom_policy import CustomFeatureExtractor
from stable_baselines3 import PPO
from utils.robot import Robot
from utils.lidar_rings import LidarRings

from utils.planner_checker import PlannerChecker
import random


GREEN_COLOR = (50, 225, 30)
BLUE_COLOR = (0, 0, 255)
BLACK_COLOR = (0, 0, 0)

SCREEN_WIDTH = 720
SCREEN_HEIGHT = 720

# SCREEN_WIDTH = 1280
# SCREEN_HEIGHT = 720

MIN_SCREEN_WIDTH = 200
MIN_SCREEN_HEIGHT = 200

class TestEnv(Env):
    def __init__(self,resizable=True) -> None:
        super(TestEnv, self).__init__()
        self.action_space_names = ["ActionXY", "ActionRot"]
        self.action_space = spaces.Box(
            low=-1, high=1, shape=(3,), dtype=np.float32)
        self.observation_space = spaces.Dict({
            'lidar': spaces.Box(low=-np.inf, high=np.inf,
                                shape=(64,64), dtype=np.float32),
            'robot': spaces.Box(low=-np.inf, high=np.inf, shape=(5,), dtype=np.float32)
        })

        obs_lst = Obstacles([SingleObstacle(0, 0, 100, 100), SingleObstacle(
            400, 400, 300, 300)])
        self.obstacles = obs_lst
        self.robot = Robot()
        self.width = SCREEN_WIDTH
        self.height = SCREEN_HEIGHT
        self.delta_t = 1
        self.n_angles = 1024
        self.lidarAngleIncrement = 0.00613592315153 # = 0.3515625 degrees
        self.lidarMinAngle = 0
        self.lidarMaxAngle =   6.27705407684847 + self.lidarAngleIncrement  # = 2*pi
        self.lidarScan = None
        self.converterCMap2D = CMap2D()
        self.converterCMap2D.set_resolution(1.)
        self.viewer = None
        self.reward = 0
        self.planner_output = {}
        self.total_reward = 0

        """frequency of rendering per each x frames.
        Defaults to 1 (render at each frame).
        """
        self.render_each = 1

        """steps the agent took during the current episode.
        re-initialzed between episodes.
        """
        self.current_episode_timesteps = 0

        """max allowed timesteps the agent is allowed to take 
        at each episode (should be set at the planner code)."""
        self.max_episode_timesteps = 1e6

        # TODO: solve the dividing by zero problem 
        self.init_difficulty = PlannerChecker().get_map_difficulity(obstacles = obs_lst, height = self.height, width = self.width,
                                                                     sx = self.robot.px, sy = self.robot.py, gx = self.robot.gx,
                                                                    gy = self.robot.gy)
        self.done = False

        # define constants 
        self.COLLISION_SCORE = -100
        self.REACHED_GOAL_SCORE = 1800
        



    def generate_obstacles_points(self):
        """Get obstacle points as flattened contours

        Returns:
            list: contours of env obstacles
        """
        self.contours = []
        for obstacle in self.obstacles.obstacles_list:
            self.contours.append(obstacle.get_points())
        self.flat_contours = flatten_contours(self.contours)
        return self.contours

    def _make_obs(self):
        """Create agent observation from environment state and LiDAR 

        Returns:
            dict: agent observation
        """
        robot = self.robot
        lidar_pos = np.array(
            [robot.px, robot.py, robot.theta], dtype=np.float32)
        ranges = np.ones((self.n_angles,), dtype=np.float32) * 25.
        angles = np.linspace(self.lidarMinAngle,
                             self.lidarMaxAngle-self.lidarAngleIncrement,
                             self.n_angles) + lidar_pos[2]
        self.generate_obstacles_points()
        render_contours_in_lidar(
            ranges, angles, self.flat_contours, lidar_pos[:2])
        self.lidar_scan = ranges
        self.lidar_angles = angles

        baselink_in_world = np.array([robot.px, robot.py, robot.theta])
        world_in_baselink = inverse_pose2d(baselink_in_world)
        # TODO: actual robot rot vel?
        robotvel_in_world = np.array([robot.vx, robot.vy, robot.w])
        robotvel_in_baselink = apply_tf_to_vel(
            robotvel_in_world, world_in_baselink)
        goal_in_world = np.array([robot.gx, robot.gy, 0])
        goal_in_baselink = apply_tf_to_pose(goal_in_world, world_in_baselink)
        robotstate_obs = np.hstack(
            [goal_in_baselink[:2], robotvel_in_baselink])
        
        lidar_rings = LidarRings(lidar_1D = self.lidar_scan , original_env_side = 720, pic_side = 64, env_size = 64*64, px = self.robot.px, py = self.robot.py)

        self.dic = lidar_rings.extract_x_and_y()
        self.pic = lidar_rings.generate_2d_lidar_pic(x = self.dic["x"], y = self.dic["y"])
        obs = {'lidar': self.pic, 'robot': robotstate_obs}

        return obs

    def render(self, close: Any = False, save_to_file: Any = False, show_score: Any = True):
        """Render robot and obstacles on an openGL window using gym viewer

        Args:
            close (bool, optional): flag to close the environment window. Defaults to False.
            save_to_file (bool, optional): flag to save render data to a file. Defaults to False.
            show_score (boo, optional): flag to show reward on window. Defaults to True.

        Returns:
            bool: flag to check the status of the openGL window
        """
        if close:
            if self.viewer is not None:
                self.viewer.close()
            return
        WINDOW_W = 1280
        WINDOW_H = 720
        VP_W = WINDOW_W
        VP_H = WINDOW_H
        from gym.envs.classic_control import rendering
        import pyglet
        from pyglet import gl
        # Create viewer
        if self.viewer is None:
            self.viewer = rendering.Viewer(WINDOW_W, WINDOW_H)
            self.transform = rendering.Transform()
            self.transform.set_scale(10, 10)
            self.transform.set_translation(128, 128)
            self.score_label = pyglet.text.Label(
                '0000', font_size=12,
                x=20, y=WINDOW_H*2.5/40.00, anchor_x='left', anchor_y='center',
                color=(255, 255, 255, 255))
            self.iteration_label = pyglet.text.Label(
                '0000', font_size=12,
                x=20, y=WINDOW_H*1.6/40.00, anchor_x='left', anchor_y='center',
                color=(255, 255, 255, 255))
            self.transform = rendering.Transform()
            self.image_lock = threading.Lock()

        def make_circle(c, r, res=10):
            """Create circle points

            Args:
                c (list): center of the circle
                r (float): radius of the circle
                res (int, optional): resolution of points. Defaults to 10.

            Returns:
                list: vertices representing with desired resolution
            """
            thetas = np.linspace(0, 2*np.pi, res+1)[:-1]
            verts = np.zeros((res, 2))
            verts[:, 0] = c[0] + r * np.cos(thetas)
            verts[:, 1] = c[1] + r * np.sin(thetas)
            return verts

        with self.image_lock:
            self.viewer.draw_circle(r=10, color=(0.3, 0.3, 0.3))
            win = self.viewer.window
            win.switch_to()
            win.dispatch_events()
            win.clear()
            gl.glViewport(0, 0, VP_W, VP_H)
            # colors
            bgcolor = np.array([0.4, 0.8, 0.4])
            obstcolor = np.array([0.3, 0.3, 0.3])
            goalcolor = np.array([1., 1., 0.3])
            goallinecolor = 0.9 * bgcolor
            nosecolor = np.array([0.3, 0.3, 0.3])
            agentcolor = np.array([0., 1., 1.])
            # Green background
            gl.glBegin(gl.GL_QUADS)
            gl.glColor4f(bgcolor[0], bgcolor[1], bgcolor[2], 1.0)
            gl.glVertex3f(0, VP_H, 0)
            gl.glVertex3f(VP_W, VP_H, 0)
            gl.glVertex3f(VP_W, 0, 0)
            gl.glVertex3f(0, 0, 0)
            gl.glEnd()
            # Transform
            rx = self.robot.px
            ry = self.robot.py
            rt = self.robot.theta
            self.transform.enable()  # applies T_sim_in_viewport to below coords (all in sim frame)
            # Map closed obstacles ---
            self.obstacle_vertices = self.generate_obstacles_points()
            for poly in self.obstacle_vertices:
                gl.glBegin(gl.GL_LINE_LOOP)
                gl.glColor4f(obstcolor[0], obstcolor[1], obstcolor[2], 1)
                for vert in poly:
                    gl.glVertex3f(vert[0], vert[1], 0)
                gl.glEnd()
            # LIDAR
            # Agent body
            for n, agent in enumerate([self.robot]):
                px = agent.px
                py = agent.py
                angle = self.robot.fix(agent.theta + np.pi/2, 2*np.pi)
                r = agent.radius
                # Agent as Circle
                poly = make_circle((px, py), r)
                gl.glBegin(gl.GL_POLYGON)
                if n == 0:
                    color = np.array([1., 1., 1.])
                else:
                    color = agentcolor
                gl.glColor4f(color[0], color[1], color[2], 1)
                for vert in poly:
                    gl.glVertex3f(vert[0], vert[1], 0)
                gl.glEnd()
                # Direction triangle
                xnose = px + r * np.cos(angle)
                ynose = py + r * np.sin(angle)
                xright = px + 0.3 * r * -np.sin(angle)
                yright = py + 0.3 * r * np.cos(angle)
                xleft = px - 0.3 * r * -np.sin(angle)
                yleft = py - 0.3 * r * np.cos(angle)
                gl.glBegin(gl.GL_TRIANGLES)
                gl.glColor4f(nosecolor[0], nosecolor[1], nosecolor[2], 1)
                gl.glVertex3f(xnose, ynose, 0)
                gl.glVertex3f(xright, yright, 0)
                gl.glVertex3f(xleft, yleft, 0)
                gl.glEnd()
            # Goal
            xgoal = self.robot.gx
            ygoal = self.robot.gy
            r = self.robot.radius

            # Goal markers
            gl.glBegin(gl.GL_TRIANGLES)
            gl.glColor4f(goalcolor[0], goalcolor[1], goalcolor[2], 1)
            triangle = make_circle((xgoal, ygoal), r, res=3)
            for vert in triangle:
                gl.glVertex3f(vert[0], vert[1], 0)
            gl.glEnd()
            # Goal line
            gl.glBegin(gl.GL_LINE_LOOP)
            gl.glColor4f(goallinecolor[0],
                         goallinecolor[1], goallinecolor[2], 1)
            gl.glVertex3f(rx, ry, 0)
            gl.glVertex3f(xgoal, ygoal, 0)
            gl.glEnd()
            # --
            self.transform.disable()
            # TODO: Add text to the env
            self.score_label.text = ""
            if show_score:
                self.score_label.text = "R {:0.4f}".format(self.reward)
                self.iteration_label.text = "iter {}".format(self.current_episode_timesteps)
            self.score_label.draw()
            self.iteration_label.draw()
            win.flip()
            if save_to_file:
                pyglet.image.get_buffer_manager().get_color_buffer().save(
                    "/tmp/navreptrainenv{:05}.png".format(self.total_steps))
            return self.viewer.isopen

    def detect_collison(self):
        """Detect if the agent has collided with any obstacle

        Returns:
            bool: flag to check collisions
        """
        ok = False
        for obstacle in self.obstacles.obstacles_list:
            distances = point_to_obstacle_distance(
                (self.robot.px, self.robot.py), (obstacle.px, obstacle.py, obstacle.width, obstacle.height))
            for dist in distances:
                ok |= (dist < self.robot.radius)
        return ok

    def _get_action(self, action: List):
        """Convert action array into action object

        Args:
            action (list): list of velocities given by agent model

        Returns:
            ActionXY : same action by given in object format 
        """
        real_angle = action[2] * np.pi  # -1, 1 -> -pi, pi
        return ActionXY(action[0], action[1], 0)

    def passed_borders(self):
        """Check if the robot passed the borders

        Returns:
            bool: flag if robot passed borders
        """
        segments = [[(0, 0), (SCREEN_WIDTH, 0)], [(SCREEN_WIDTH, 0), (SCREEN_WIDTH, SCREEN_HEIGHT)], [
            (SCREEN_WIDTH, SCREEN_HEIGHT), (0, SCREEN_HEIGHT)], [(0, SCREEN_HEIGHT), (0, 0)]]
        ok = False
        for segment in segments:
            dist = point_to_segment_distance(
                segment[0], segment[1], (self.robot.px, self.robot.py))
            ok |= (dist <= self.robot.radius)
        return ok

    def step(self, action: List):
        """Step into the new state using an action given by the agent model

        Args:
            action (list): velocity action (vx, vy) provided by the agent model

        Returns:
            dict : environment state as the agent observation
        """
        print("step starting")
        # if something happens, don't do another step
        if self.done:
            return self._make_obs(), self.reward, self.done, {'episode_number': 1}

        # increase counters 
        self.current_episode_timesteps += 1

        # convert action
        action = self._get_action(action)

        # add distance differential
        old_distance_to_goal = point_to_point_distance(
            (self.robot.px, self.robot.py), (self.robot.gx, self.robot.gy))
        self.robot.step(action, self.delta_t)

        new_distance_to_goal = point_to_point_distance(
            (self.robot.px, self.robot.py), (self.robot.gx, self.robot.gy))

        self.reward += (old_distance_to_goal - new_distance_to_goal)
        
        """
        Reward = distance differential - 100 * collion_flag + 1800 + goal_flag
        distance differential = +/- for one step closer/away from goal
        """
        
        # if collision detected, add -100 and raise done flag
        if (self.detect_collison() or self.passed_borders()):
            print("----------------COLLISION DETECTED-------------------")
            self.reward += self.COLLISION_SCORE
            self.done = True
            self.success_flag = False

        # if reached goal, add 1800 and raise sucess/done flags
        if not self.done and self.robot.reached_destination(): 
                self.reward += 1800 # 1748 is the max reward it can get from following the longest path possible
                self.done = True
                self.success_flag = True

        if not self.done and self.current_episode_timesteps >= self.max_episode_timesteps:
            self.done = True
            self.success_flag = False

        if self.current_episode_timesteps % self.render_each == 0:
            self.render()
        
        self.total_reward += self.reward

        print("step Ended")
        return self._make_obs(), self.reward, self.done, {"episode_number": 1}
    def reset(self):
        """
        Reset robot state and generate new obstacles points
        Returns:
            dict: observation of the current environment state
        """
        self.reward = 0
        self.success_flag = False
        self.generate_obstacles_points()
        return self._make_obs()
