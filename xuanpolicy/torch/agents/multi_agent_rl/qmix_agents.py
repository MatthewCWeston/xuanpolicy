from xuanpolicy.torch.agents import *


class QMIX_Agents(MARLAgents):
    def __init__(self,
                 config: Namespace,
                 envs: DummyVecEnv_Pettingzoo,
                 device: Optional[Union[int, str, torch.device]] = None):
        self.gamma = config.gamma
        self.start_greedy, self.end_greedy = config.start_greedy, config.end_greedy
        self.egreedy = self.start_greedy
        self.delta_egreedy = (self.start_greedy - self.end_greedy) / (
                config.decay_step_greedy / envs.num_envs / envs.max_episode_length)

        if config.state_space is not None:
            config.dim_state, state_shape = config.state_space.shape, config.state_space.shape
        else:
            config.dim_state, state_shape = None, None

        input_representation = get_repre_in(config)
        self.use_recurrent = config.use_recurrent
        if self.use_recurrent:
            kwargs_rnn = {"N_recurrent_layers": config.N_recurrent_layers,
                          "dropout": config.dropout,
                          "rnn": config.rnn}
            representation = REGISTRY_Representation[config.representation](*input_representation, **kwargs_rnn)
        else:
            representation = REGISTRY_Representation[config.representation](*input_representation)
        mixer = QMIX_mixer(config.dim_state[0], config.hidden_dim_mixing_net, config.hidden_dim_hyper_net,
                           config.n_agents, device)
        input_policy = get_policy_in_marl(config, representation, mixer=mixer)
        policy = REGISTRY_Policy[config.policy](*input_policy,
                                                use_recurrent=config.use_recurrent,
                                                rnn=config.rnn)
        optimizer = torch.optim.Adam(policy.parameters(), config.learning_rate, eps=1e-5)
        scheduler = torch.optim.lr_scheduler.LinearLR(optimizer, start_factor=1.0, end_factor=0.5,
                                                      total_iters=get_total_iters(config.agent_name, config))
        self.observation_space = envs.observation_space
        self.action_space = envs.action_space
        self.representation_info_shape = policy.representation.output_shapes
        self.auxiliary_info_shape = {}

        if self.use_recurrent:
            memory = MARL_OffPolicyBuffer_RNN(config.n_agents,
                                              state_shape,
                                              config.dim_obs,
                                              config.dim_act,
                                              config.rew_shape,
                                              envs.num_envs,
                                              config.buffer_size,
                                              envs.max_episode_length,
                                              config.batch_size)
        else:
            memory = MARL_OffPolicyBuffer(state_shape,
                                          config.obs_shape,
                                          config.act_shape,
                                          config.rew_shape,
                                          config.done_shape,
                                          envs.num_envs,
                                          config.buffer_size,
                                          config.batch_size)
        learner = QMIX_Learner(config, policy, optimizer, scheduler,
                               config.device, config.model_dir, config.gamma,
                               config.sync_frequency)
        super(QMIX_Agents, self).__init__(config, envs, policy, memory, learner, device,
                                          config.log_dir, config.model_dir)

    def act(self, obs_n, *rnn_hidden, avail_actions=None, test_mode=False):
        batch_size = obs_n.shape[0]
        agents_id = torch.eye(self.n_agents).unsqueeze(0).expand(batch_size, -1, -1).to(self.device)
        obs_in = torch.Tensor(obs_n).view([batch_size, self.n_agents, -1]).to(self.device)
        if self.use_recurrent:
            hidden_state, greedy_actions, _ = self.policy(obs_in, agents_id, *rnn_hidden, avail_actions=avail_actions)
        else:
            hidden_state, greedy_actions, _ = self.policy(obs_in, agents_id, avail_actions=avail_actions)
        greedy_actions = greedy_actions.cpu().detach().numpy()

        if test_mode:
            return hidden_state, greedy_actions
        else:
            if avail_actions is None:
                random_actions = np.random.choice(self.dim_act, [self.nenvs, self.n_agents])
            else:
                random_actions = Categorical(torch.Tensor(avail_actions)).sample().numpy()
            if np.random.rand() < self.egreedy:
                return hidden_state, random_actions
            else:
                return hidden_state, greedy_actions

    def train(self, i_step):
        if self.egreedy >= self.end_greedy:
            self.egreedy -= self.delta_egreedy

        if i_step > self.start_training:
            sample = self.memory.sample()
            if self.use_recurrent:
                info_train = self.learner.update_recurrent(sample)
            else:
                info_train = self.learner.update(sample)
            info_train["epsilon-greedy"] = self.egreedy
            return info_train
        else:
            return {}
