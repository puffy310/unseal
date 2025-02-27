# pre-implemented common hooks
import math
from typing import Iterable, Callable, Optional, Union, List, Tuple, Dict

import einops
import torch
import torch.nn.functional as F
from transformers.models.gpt_neo.modeling_gpt_neo import GPTNeoPreTrainedModel
from tqdm import tqdm
from . import util
from .commons import Hook, HookedModel

def save_output(save_ctx: dict, input: torch.Tensor, output: torch.Tensor):
    """Basic hooking function for saving the output of a module to the global context object

    :param save_ctx: Context object
    :type save_ctx: dict
    :param input: Input to the module.
    :type input: torch.Tensor
    :param output: Output of the module.
    :type output: torch.Tensor
    """
    if isinstance(output, torch.Tensor):
        save_ctx['output'] = output.to('cpu')
    elif isinstance(output, Iterable): # hope for the best
        save_ctx['output'] = util.recursive_to_device(output, 'cpu')


def replace_activation(indices: str, replacement_tensor: torch.Tensor) -> Callable:
    """Creates a hook which replaces a module's activation (output) with a replacement tensor. 
    If there is a dimension mismatch, the replacement tensor is copied along the leading dimensions of the output.

    Example: If the activation has shape ``(B, T, D)`` and replacement tensor has shape ``(D,)`` which you want to plug in
    at position t in the T dimension for every tensor in the batch, then indices should be ``:,t,:``. 

    :param indices: Indices at which to insert the replacement tensor
    :type indices: str
    :param replacement_tensor: Tensor that is filled in.
    :type replacement_tensor: torch.Tensor
    :return: Function that replaces part of a given tensor with replacement_tensor
    :rtype: Callable
    """
    slice_ = util.create_slice(indices)
    def func(save_ctx, input, output):
        # add dummy dimensions if shape mismatch
        diff = len(output[slice_].shape) - len(replacement_tensor.shape)
        rep = replacement_tensor[(None,)*diff].to(output.device)
        # replace part of tensor
        output[slice_] = rep
        return output

    return func

def transformers_get_attention(heads: Optional[Union[int, Iterable[int], str]] = None) -> Callable:
    # convert string to slice
    if heads is None:
        heads = ":"
    if isinstance(heads, str):
        heads = util.create_slice(heads)

    def func(save_ctx, input, output):
        save_ctx['attn'] = output[2][:,heads,...].detach().cpu()
    
    return func

def gpt_get_attention_hook(layer: int, key: str, heads: Optional[Union[int, Iterable[int], str]] = None) -> Callable:
    func = transformers_get_attention(heads)
    return Hook(f'transformer->h->{layer}->attn', func, key)


def logit_hook(
    layer:int, 
    model: HookedModel, 
    target: Optional[Union[int, List[int]]] = None, 
    position: Optional[Union[int, List[int]]] = None,
    key: Optional[str] = None,
    split_heads: Optional[bool] = False,
) -> Hook:
    """Create a hook that saves the logits of a layer's output.
    Outputs are saved to save_ctx['{layer}_logits']['logits'].
    
    Currently only works with GPT like models, since it assumes the key of the embedding matrix and the structure of
    these models.

    :param layer: The number of the layer
    :type layer: int
    :param model: The model.
    :type model: HookedModel
    :param target: The target token(s) to extract logits for. Defaults to all tokens.
    :type target: Union[int, List[int]]
    :param position: The position for which to extract logits for. Defaults to all positions.
    :type position: Union[int, List[int]]
    :param key: The key of the hook. Defaults to {layer}_logits.
    :type key: str
    :param split_heads: Whether to split the heads. Defaults to False.
    :type split_heads: bool
    :return: The hook.
    :rtype: Hook
    """
    
    # generate slice
    if target is None:
        target = ":"
    else:
        if isinstance(target, int):
            target = str(target)
        else:
            target = "[" + ",".join(str(t) for t in target) + "]"
    if position is None:
        position = ":"
    else:
        if isinstance(position, int):
            position = str(position)
        else:
            position = "[" + ",".join(str(p) for p in position) + "]"
    position_slice = util.create_slice(f":,{position},:")
    target_slice = util.create_slice(f"{target},:")
    
    # load the relevant part of the vocab matrix
    vocab_matrix = model.structure['children']['lm_head']['module'].weight[target_slice].T
        
    if split_heads:
        if isinstance(model.model, GPTNeoPreTrainedModel):
            head_dim = model.model.transformer.h[layer].attn.attention.head_dim
        else:
            head_dim = model.model.transformer.h[layer].attn.head_dim
        vocab_matrix = einops.rearrange(vocab_matrix, '(num_heads head_dim) vocab_size -> num_heads head_dim vocab_size', head_dim=head_dim)
    
    def inner(save_ctx, input, output):
        if split_heads:
            einsum_in = einops.rearrange(output[0][position_slice], 'batch seq_len (heads head_dim) -> batch heads seq_len head_dim', head_dim=head_dim)
            einsum_out = torch.einsum('bcij,cjk->bcik', einsum_in, vocab_matrix)
        else:
            einsum_in = output[0][position_slice]
            einsum_out = torch.einsum('bij,jk->bik', einsum_in, vocab_matrix)
            
        save_ctx['logits'] = einsum_out.detach().cpu()
    
    # write key
    if key is None:
        key = str(layer) + '_logits'
    
    # create hook
    hook = Hook(f'transformer->h->{layer}', inner, key)
    
    return hook

def gpt_attn_wrapper(
    func: Callable, 
    save_ctx: Dict, 
    c_proj: torch.Tensor, 
    vocab_embedding: torch.Tensor, 
    target_ids: torch.Tensor,
    batch_size: int = 16,
) -> Tuple[Callable, Callable]:
    """Wraps around the [AttentionBlock]._attn function to save the individual heads' logits.
    This is necessary because the individual heads' logits are not available on a module level and thus not accessible via a hook.

    :param func: original _attn function
    :type func: Callable
    :param save_ctx: context to which the logits will be saved
    :type save_ctx: Dict
    :param c_proj: projection matrix, this is W_O in Anthropic's terminology
    :type c_proj: torch.Tensor
    :param vocab_matrix: vocabulary/embedding matrix, this is W_V in Anthropic's terminology
    :type vocab_matrix: torch.Tensor
    :param target_ids: indices of the target tokens for which the logits are computed
    :type target_ids: torch.Tensor
    :param batch_size: batch size to reduce compute cost
    :type batch_size: int
    :return: inner, func, the wrapped function and the original function
    :rtype: Tuple[Callable, Callable]
    """
    # TODO Find a smarter/more efficient way of implementing this function
    def inner(query, key, value, *args, **kwargs):
        nonlocal c_proj
        nonlocal target_ids
        nonlocal vocab_embedding
        attn_output, attn_weights = func(query, key, value, *args, **kwargs)
        with torch.no_grad():
            temp = attn_weights[...,None] * value[:,:,None]
            if len(c_proj.shape) == 2:
                c_proj = einops.rearrange(c_proj, '(head_dim num_heads) out_dim -> head_dim num_heads out_dim', num_heads=attn_output.shape[1])
            c_proj = einops.rearrange(c_proj, 'h n o -> n h o')
            temp = temp[0,:,:-1] # could this be done earlier?
            new_temp = []
            for head in tqdm(range(temp.shape[0])):
                for i in range(math.ceil(temp.shape[1] / batch_size)):
                    out = temp[head, i*batch_size:(i+1)*batch_size] @ c_proj[head]
                    out = out @ vocab_embedding # compute logits
                    out -= out.mean(dim=-1, keepdim=True) # center logits
                    # select targets
                    out = out[...,torch.arange(len(target_ids)), target_ids].to('cpu')
                    new_temp.append(out)
            new_temp = torch.cat(new_temp, dim=0)        
            new_temp = einops.rearrange(new_temp, '(h t1) t2 -> h t1 t2', h=temp.shape[0], t1=len(target_ids), t2=len(target_ids))
            max_pos_value = torch.amax(new_temp).item()
            max_neg_value = torch.amax(-new_temp).item()
            
            save_ctx['logits'] = {
                'pos': (new_temp/max_pos_value).clamp(min=0, max=1).detach(),
                'neg': (new_temp/max_neg_value).clamp(min=-1, max=0).detach(),
            }         
        return attn_output, attn_weights
    return inner, func
