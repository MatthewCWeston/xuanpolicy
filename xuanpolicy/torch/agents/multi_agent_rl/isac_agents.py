from xuanpolicy.torch.agents import *


class ISAC_Agents(MARLAgents):
    def __init__(self,
                 config: Namespace,
                 envs: DummyVecEnv_Pettingzoo,
                 device: Optional[Union[int, str, torch.device]] = None):
        self.gamma = config.gamma

        input_representation = get_repre_in(config)
        representation = REGISTRY_Representation[config.representation](*input_representation)
        input_policy = get_policy_in_marl(config, representation, config.agent_keys)
        policy = REGISTRY_Policy[config.policy](*input_policy)
        optimizer = [torch.optim.Adam(policy.parameters_actor, config.lr_a, eps=1e-5),
                     torch.optim.Adam(policy.parameters_critic, config.lr_c, eps=1e-5)]
        scheduler = [torch.optim.lr_scheduler.LinearLR(optimizer[0], start_factor=1.0, end_factor=0.5,
                                                       total_iters=get_total_iters(config.agent_name, config)),
                     torch.optim.lr_scheduler.LinearLR(optimizer[1], start_factor=1.0, end_factor=0.5,
                                                       total_iters=get_total_iters(config.agent_name, config))]
        self.observation_space = envs.observation_space
        self.action_space = envs.action_space
        self.actions_high, self.actions_low = [], []
        for k in config.agent_keys:
            self.actions_high.append(self.action_space[k].high)
            self.actions_low.append(self.action_space[k].low)
        self.actions_high, self.actions_low = np.array(self.actions_high), np.array(self.actions_low)
        self.representation_info_shape = policy.representation.output_shapes
        self.auxiliary_info_shape = {}

        if config.state_space is not None:
            config.dim_state, state_shape = config.state_space.shape, config.state_space.shape
        else:
            config.dim_state, state_shape = None, None
        memory = MARL_OffPolicyBuffer(state_shape,
                                      config.obs_shape,
                                      config.act_shape,
                                      config.rew_shape,
                                      config.done_shape,
                                      envs.num_envs,
                                      config.buffer_size,
                                      config.batch_size)
        learner = ISAC_Learner(config, policy, optimizer, scheduler,
                               config.device, config.model_dir, config.gamma)
        super(ISAC_Agents, self).__init__(config, envs, policy, memory, learner, device,
                                          config.log_dir, config.model_dir)

    def act(self, obs_n, episode, test_mode, state=None, noise=False):
        batch_size = len(obs_n)
        agents_id = torch.eye(self.n_agents).unsqueeze(0).expand(batch_size, -1, -1).to(self.device)
        _, dists = self.policy(obs_n, agents_id)
        acts = dists.rsample()
        actions = acts.cpu().detach().numpy()
        actions = np.clip(actions, self.actions_low, self.actions_high)
        return actions

    def train(self, i_episode):
        if self.memory.can_sample(self.args.batch_size):
            sample = self.memory.sample()
            info_train = self.learner.update(sample)
            return info_train
        else:
            return {}
