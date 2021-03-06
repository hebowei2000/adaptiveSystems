import torch
import torch.optim as optim
import gym
from dqn import ReplayBuffer
from torch.distributions import Categorical
from torch.nn.functional import mse_loss
import numpy as np
from torch.optim.lr_scheduler import StepLR
from torch.optim.lr_scheduler import ReduceLROnPlateau
from actor_critic_structure import Actor, Critic
from copy import deepcopy


# In[]:

class ActorReplayBuffer:
    def __init__(self, max_size=2000):
        self.max_size = max_size
        self.target = []
        self.predicted = []
        self.gradient = []

    def __len__(self):
        return len(self.target)

    def add(self, target, predicted, gradient):
        self.target.append(target)
        self.predicted.append(predicted)
        self.gradient.append(gradient)

    def sample(self, sample_size=32):
        sample_objectives = {}
        if self.__len__() >= sample_size:
            # pick up only random 32 events from the memory
            indices = np.random.choice(self.__len__(), size=sample_size)
            sample_objectives['target'] = torch.stack(self.target)[indices].squeeze(-1)
            sample_objectives['predicted'] = torch.stack(self.predicted)[indices].squeeze(-1)
            sample_objectives['gradient'] = torch.stack(self.gradient)[indices].squeeze(-1)
        else:
            # if the current buffer size is not greater than 32 then pick up the entire memory
            sample_objectives['target'] = torch.stack(self.target).squeeze(-1)
            sample_objectives['predicted'] = torch.stack(self.predicted).squeeze(-1)
            sample_objectives['gradient'] = torch.stack(self.gradient).squeeze(-1)

        return sample_objectives

# In[]:
# TODO :
#  1. Use dropouts
#  2. fix targets in critic, should this be done for actor as well?

actor_learning_rate = 1e-2
critic_learning_rate = 1e-2
train_episodes = 5000

env = gym.make('CartPole-v1')
actor = Actor(input_size=env.observation_space.shape[0], output_size=env.action_space.n, hidden_size=24)

# Approximating the Value function
critic = Critic(input_size=env.observation_space.shape[0], output_size=1, hidden_size=24)
# critic_old is used for fixing the target in learning the V function
critic_old = deepcopy(critic)
copy_epoch = 100

optimizer_algo = 'batch'

# Critic is always optimized in batch
critic_optimizer = optim.Adam(critic.parameters(), lr=critic_learning_rate)

# actor is optimized either in batch or sgd
if optimizer_algo == 'sgd':
    actor_optimizer = optim.SGD(actor.parameters(), lr=actor_learning_rate, momentum=0.8, nesterov=True)
elif optimizer_algo == 'batch':
    actor_optimizer = optim.Adam(actor.parameters(), lr=actor_learning_rate)

# gamma = decaying factor
actor_scheduler = StepLR(actor_optimizer, step_size=500, gamma=0.1)
critic_scheduler = StepLR(critic_optimizer, step_size=500, gamma=0.1)


gamma = 0.99
avg_history = {'episodes': [], 'timesteps': [], 'reward': []}
agg_interval = 10
avg_reward = 0.0
avg_timestep = 0
running_loss1_mean = 0
running_loss2_mean = 0
loss1_history = []
loss2_history = []
# initialize policy and replay buffer
replay_buffer = ReplayBuffer()
actor_replay_buffer = ActorReplayBuffer()


# In[]:


def update_critic(cur_states, actions, next_states, rewards, dones):

    # target doesnt change when its terminal, thus multiply with (1-done)
    targets = rewards + torch.mul(1 - dones, gamma*critic(next_states).squeeze(-1) )
    # expanded_targets are the Q values of all the actions for the current_states sampled
    # from the previous experience. These are the predictions
    expanded_targets = critic(cur_states).squeeze(-1)
    critic_optimizer.zero_grad()
    loss1 = mse_loss(input=expanded_targets, target=targets)
    loss1.backward()
    critic_optimizer.step()
    return loss1.item()


# In[]:

# Train the network to predict actions for each of the states
for episode_i in range(train_episodes):

    # make a copy every copy_epoch epochs
    if episode_i % copy_epoch == 0:
        critic_old = deepcopy(critic)

    episode_timestep = 0
    episode_reward = 0.0

    done = False
    cur_state = torch.Tensor(env.reset())

    log_prob_list = torch.Tensor()
    u_value_list = torch.Tensor()
    target_list = torch.Tensor()

    while not done:
        action, log_prob = actor.select_action(cur_state)

        # take action in the environment
        next_state, reward, done, info = env.step(action.item())
        next_state = torch.Tensor(next_state)

        if done:
            reward = -500
        else:
            reward = 20

        u_value = critic(cur_state)
        # Update parameters of critic by TD(0)
        # TODO : Use TD Lambda here and compare the performance

        # TODO : Uncomment this line if 1-done is a wrong concept in actor
        # target = reward + gamma * critic(next_state)
        # Using 1-done even in the target for actor since the next state wont have any meaning when done=1
        # TODO : Remove this line if 1-done is a wrong concept in actor
        target = reward + gamma * (1-done) * critic(next_state)


        # TODO : Checking if removing replay buffer and updating Q in batches improves anything
        replay_buffer.add(cur_state, action, next_state, reward, done)
        # sample minibatch of transitions from the replay buffer
        # the sampling is done every timestep and not every episode
        sample_transitions = replay_buffer.sample_pytorch()
        # update the critic's q approximation using the sampled transitions
        running_loss1_mean += update_critic(**sample_transitions)

        # this section was for actor experience replay, which to my dismay performed much worse than without replay
        # actor_replay_buffer.add(target, u_value, -log_prob)
        # sample_objectives = actor_replay_buffer.sample(sample_size=32)
        # actor_optimizer.zero_grad()
        # # compute the gradient from the sampled log probability
        # #  the log probability times the Q of the action that you just took in that state
        # """Important note"""
        # # Reward scaling, this performs much better.
        # # In the general case this might not be a good idea. If there are rare events with extremely high rewards
        # # that only occur in some episodes, and the majority of episodes only experience common events with
        # # lower-scale rewards, then this trick will mess up training. In cartpole environment this is not of concern
        # # since all the rewards are 1 itself
        # multiplication_factor = sample_objectives['target'] - sample_objectives['predicted']
        # multiplication_factor = (multiplication_factor - multiplication_factor.mean() ) / ( multiplication_factor.std(unbiased=False) + 1e-8)
        # loss2 = torch.sum(torch.mul(sample_objectives['gradient'], multiplication_factor))  # the advantage function used is the TD error
        # loss2.backward(retain_graph=True)
        # running_loss2_mean += loss2.item()
        # actor_optimizer.step()

        if optimizer_algo == 'sgd':
            # Update parameters of actor by policy gradient
            actor_optimizer.zero_grad()
            # compute the gradient from the sampled log probability
            #  the log probability times the Q of the action that you just took in that state
            # TODO : the target here is still a moving target, see if fixing this for sometime leads to any improvement
            loss2 = -log_prob * (target - u_value) # the advantage function used is the TD error
            loss2.backward()
            running_loss2_mean += loss2.item()
            actor_optimizer.step()

        elif optimizer_algo == 'batch':
            target_list = torch.cat([target_list, target])
            u_value_list = torch.cat([u_value_list, u_value])
            log_prob_list = torch.cat([log_prob_list, log_prob.reshape(-1)])

        episode_reward += reward
        episode_timestep += 1
        cur_state = next_state


    # TODO : Remove this if it doesnt improve the convergence
    # critic_optimizer.zero_grad()
    # u_value_list_copy = (u_value_list - u_value_list.mean()) / u_value_list.std()
    # target_list_copy = (target_list - target_list.mean()) / target_list.std()
    # loss1 = mse_loss(input=u_value_list_copy, target=target_list_copy)
    # loss1.backward(retain_graph=True)
    # running_loss1_mean += loss1.item()
    # critic_optimizer.step()


    if optimizer_algo == 'batch':
        # Update parameters of actor by policy gradient
        actor_optimizer.zero_grad()
        # compute the gradient from the sampled log probability
        #  the log probability times the Q of the action that you just took in that state

        """Important note"""
        # Reward scaling, this performs much better.
        # In the general case this might not be a good idea. If there are rare events with extremely high rewards
        # that only occur in some episodes, and the majority of episodes only experience common events with
        # lower-scale rewards, then this trick will mess up training. In cartpole environment this is not of concern
        # since all the rewards are 1 itself
        multiplication_factor = target_list - u_value_list
        multiplication_factor = (multiplication_factor - multiplication_factor.mean() ) / multiplication_factor.std()
        loss2 = torch.sum(torch.mul(-log_prob_list, multiplication_factor))  # the advantage function used is the TD error

        loss2.backward()
        running_loss2_mean += loss2.item()
        actor_optimizer.step()

    loss1_history.append(running_loss1_mean/episode_timestep)
    loss2_history.append(running_loss2_mean/episode_timestep)
    running_loss1_mean = 0
    running_loss2_mean = 0

    avg_reward += episode_reward
    avg_timestep += episode_timestep

    avg_history['episodes'].append(episode_i + 1)
    avg_history['timesteps'].append(avg_timestep)
    avg_history['reward'].append(avg_reward)
    avg_timestep = 0
    avg_reward = 0.0

    actor_scheduler.step()
    critic_scheduler.step()

    if (episode_i + 1) % agg_interval == 0:
        print('Episode : ', episode_i+1,
              'actor lr : ', actor_scheduler.get_lr(), 'critic lr : ', critic_scheduler.get_lr(),
              'Actor Objective : ', loss2_history[-1], 'Critic Loss', loss1_history[-1],
              'Avg Timestep : ', avg_history['timesteps'][-1])


# In[]:
import matplotlib.pyplot as plt

fig, axes = plt.subplots(nrows=2, ncols=2, figsize=(12, 7))
plt.subplots_adjust(wspace=0.5)
axes[0][0].plot(avg_history['episodes'], avg_history['timesteps'])
axes[0][0].set_title('Timesteps per episode')
axes[0][0].set_ylabel('Timesteps')
axes[0][1].plot(avg_history['episodes'], avg_history['reward'])
axes[0][1].set_title('Reward per episode')
axes[0][1].set_ylabel('Reward')
axes[1][0].set_title('Critic Loss')
axes[1][0].plot(loss1_history)
axes[1][1].set_title('Actor Objective')
axes[1][1].plot(loss2_history)

plt.show()

# In[]:

cur_state = env.reset()
total_step = 0
total_reward = 0.0
done = False
while not done:
    action, probs = actor.select_action(torch.Tensor(cur_state))
    next_state, reward, done, info = env.step(action.item())
    total_reward += reward
    env.render(mode='rgb_array')
    total_step += 1
    cur_state = next_state
print("Total timesteps = {}, total reward = {}".format(total_step, total_reward))
env.close()
