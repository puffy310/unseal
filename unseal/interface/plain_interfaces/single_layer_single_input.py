def layer_change():
    utils.text_change(0)
    
import json
import time

import streamlit as st

from unseal.interface import utils
from unseal.interface import interface_setup as setup
from unseal.interface.commons import SESSION_STATE_VARIABLES

# perform startup tasks
setup.startup(SESSION_STATE_VARIABLES, './registered_models.json')

# create sidebar
with st.sidebar:
    setup.create_sidebar()
    
    sample = st.checkbox('Enable sampling', value=False, key='sample')
    if sample:
        setup.create_sample_sliders()
        setup.on_sampling_config_change()
    
    if "storage" not in st.session_state:
        st.session_state["storage"] = [""]
    
    # select layer
    if st.session_state.num_layers is None:
        options = list()
    else:
        options = list(range(st.session_state.num_layers))
    st.selectbox('Layer', options=options, key='layer', on_change=layer_change, index=0)
    
    # input 1
    placeholder = st.empty()
    placeholder.text_area(label='Input', on_change=utils.on_text_change, key='input_text', value=st.session_state.storage[0], kwargs=dict(col_idx=0, text_key='input_text'))
    if sample:
        st.button(label="Sample", on_click=utils.sample_text, kwargs=dict(col_idx=0, key="input_text"), key="sample_text")
    
    # sometimes need to force a re-render
    st.button('Show Attention', on_click=utils.text_change, kwargs=dict(col_idx=0))
    
    f =  json.encoder.JSONEncoder().encode(st.session_state.visualization)
    st.download_button(
        label='Download Visualization', 
        data=f, 
        file_name=f'{st.session_state.model_name}_{time.strftime("%Y%m%d_%H%M%S", time.localtime())}.json', 
        mime='application/json', 
        help='Download the visualizations as a json of html files.', 
        key='download_button'
    )

# show the html visualization
if st.session_state.model is not None:
    cols = st.columns(1)
    for col_idx, col in enumerate(cols):
        if f"col_{col_idx}" in st.session_state.visualization:
            with col:
                with st.expander(f'Layer {st.session_state.layer}'):
                    st.components.v1.html(st.session_state.visualization[f"col_{col_idx}"][f"layer_{st.session_state.layer}"], height=600)