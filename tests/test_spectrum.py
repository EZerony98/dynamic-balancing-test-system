import numpy as np
from services.spectrum import frequency_component, pulse_frequency, spectrum, sampling_budget
from models.data import ChannelCalibration

def test_frequency_within_one_bin():
    fs=100000;n=8192;f0=1234.;x=np.sin(2*np.pi*f0*np.arange(n)/fs);f,a,m=spectrum(x,fs,n)
    assert abs(m.dominant_frequency-f0)<=fs/n
def test_gains():
    for g in [1,1.99,4.98,10,25.7]: assert np.allclose(ChannelCalibration("AIN0",g).convert(np.array([g])),[1])
def test_budget():
    assert sampling_budget(100000,1,0)[0];assert not sampling_budget(100000,2,0)[0]

def test_pulse_frequency_one_pulse_per_revolution():
    fs=30000.;f0=125.;t=np.arange(6000)/fs;pulse=(np.sin(2*np.pi*f0*t)>.95).astype(float)
    assert abs(pulse_frequency(pulse,fs)-f0)<1.0

def test_single_frequency_component():
    fs=30000.;f0=125.;t=np.arange(6000)/fs;x=.2*np.sin(2*np.pi*f0*t+np.deg2rad(30))
    component=frequency_component(x,fs,f0)
    assert abs(component.amplitude-.2)<.005
