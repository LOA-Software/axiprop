# Copyright 2020
# Authors: Igor Andriyash
# License: GNU GPL v3
"""
Axiprop main file

This file contains main classes of axiprop:
- PropagatorCommon
- PropagatorSymmetric
- PropagatorResampling
- PropagatorFFT2
"""
import numpy as np
from scipy.special import jn, jn_zeros
import os
from .backends import AVAILABLE_BACKENDS, backend_strings_ordered

try:
    from tqdm.auto import tqdm
    tqdm_available = True
    bar_format='{l_bar}{bar}| {elapsed}<{remaining} [{rate_fmt}{postfix}]'
except Exception:
    tqdm_available = False


class PropagatorCommon:
    """
    Base class for propagators. Contains methods to:
    - initialize the backend;
    - setup spectral `kz` grid;
    - setup radial `r` and spectral `kr` grids;
    - setup transverse `x`-`y`, and spectral `kx`-`ky` grids;
    - perform a single-step calculation;
    - perform a multi-step calculation;

    This class should to be used to derive the actual Propagators
    by adding proper methods for the Transverse Spectral Transforms (TST).
    """

    def init_backend(self, backend, verbose=True):

        if verbose:
            print('Available backends are: ' \
                + ', '.join(AVAILABLE_BACKENDS.keys()))

        if backend is not None:
            backend_string = backend
        elif 'AXIPROP_BACKEND' in os.environ:
            backend_string = os.environ['AXIPROP_BACKEND']
        else:
            for bknd_str in backend_strings_ordered:
                if bknd_str in AVAILABLE_BACKENDS:
                    backend_string = bknd_str
                    break

        if backend_string not in AVAILABLE_BACKENDS:
            raise Exception(f'Backend {backend_string} is not available')

        self.bcknd = AVAILABLE_BACKENDS[backend_string]()
        if verbose:
            print(f'{self.bcknd.name} is chosen')

    def init_kz(self, kz_axis):
        """
        Setup `kz` spectral grid.

        Parameters
        ----------
        kz_axis: a tuple (k0, Lkz, Nkz) or a 1D numpy.array
            When tuple is given the axis is created using:

              k0: float (1/m)
                Central wavenumber of the spectral domain.

              Lkz: float (1/m)
                Total spectral width in units of wavenumbers.

              Nkz: int
                Number of spectral modes (wavenumbers) to resolve the temporal
                profile of the wave.
        """
        if type(kz_axis) is tuple:
            k0, Lkz, Nkz = kz_axis
            Nkz_2 = int(np.ceil(Nkz/2))
            half_ax = np.linspace(0, 1., Nkz_2)
            full_ax = np.r_[-half_ax[1:][::-1], half_ax]
            self.Nkz = full_ax.size
            self.kz = k0 + Lkz / 2 * full_ax
        else:
            self.kz = kz_axis.copy()
            self.Nkz = self.kz.size

    def init_kr(self, Rmax, Nr):
        """
        Setup spectral `kr` grid and related data
        """
        mode = self.mode

        if mode !=0:
            alpha = np.r_[0., jn_zeros(mode, Nr)]
        else:
            alpha = jn_zeros(mode, Nr+1)

        self.alpha_np1 = alpha[-1]
        self.alpha = alpha[:-1]
        self.kr = self.alpha / Rmax
        self.kr2 = self.bcknd.to_device( self.kr**2 )

    def init_r_sampled(self, r_axis):
        """
        Setup the radial `r` grid from an array

        Parameters
        ----------
        r_axis: float ndarray (m)
        """
        r = r_axis.copy()
        Nr = r.size
        dr_est = (r[1:] - r[:-1]).mean()
        Rmax = r.max() + dr_est/2
        return r, Rmax, Nr

    def init_r_symmetric(self, r_axis):
        """
        Setup radial `r` grid on jn-roots

        Parameters
        ----------
        r_axis: tuple (Rmax, Nr)
          Here:
            Rmax: float (m)
                Radial size of the calculation domain.

            Nr: int
                Number of nodes of the radial grid.
        """
        Rmax, Nr = r_axis

        alpha = jn_zeros(self.mode, Nr+1)
        alpha_np1 = alpha[-1]
        alpha = alpha[:-1]
        r = Rmax * alpha / alpha_np1
        return r, Rmax, Nr

    def init_r_uniform(self, r_axis):
        """
        Setup a uniform radial `r` grid

        Parameters
        ----------
        r_axis: tuple (Rmax, Nr)
          Here:
            Rmax: float (m)
                Radial size of the calculation domain.

            Nr: int
                Number of nodes of the radial grid.
        """
        Rmax, Nr = r_axis
        r = np.linspace(0, Rmax, Nr, endpoint=False)
        dr = r[[0,1]].ptp()
        r += 0.5 * dr
        return r, Rmax, Nr

    def apply_boundary(self, u, nr_boundary=16):
        # apply the boundary "absorbtion"
        absorb_layer_axis = np.r_[0 : np.pi/2 : nr_boundary*1j]
        absorb_layer_shape = np.cos(absorb_layer_axis)**0.5
        absorb_layer_shape[-1] = 0.0
        u[:, -nr_boundary:] *= absorb_layer_shape
        return u

    def init_xykxy_fft2(self, x_axis, y_axis):
        """
        Setup the transverse `x` and `y` and corresponding spectral
        `kx` and `ky` grids, and fix data type.

        Parameters
        ----------
        x_axis: tuple (Lx, Nx)
          Define the x-axis grid with parameters:
            Lx: float (m)
                Full size of the calculation domain along x-axis.

            Nx: int
                Number of nodes of the x-grid. Better be an odd number,
                in order to make a symmteric grid.

        y_axis: tuple (Ly, Ny)
          Define the y-axis grid with parameters:
            Ly: float (m)
                Full size of the calculation domain along y-axis.

            Ny: int
                Number of nodes of the y-grid.Better be an odd number,
                in order to make a symmteric grid.
        """
        Lx, Nx = x_axis
        Ly, Ny = y_axis

        self.Lx = Lx
        self.Ly = Ly
        self.Nx = Nx
        self.Ny = Ny

        self.x = np.linspace(-Lx/2, Lx/2, Nx)
        self.y = np.linspace(-Ly/2, Ly/2, Ny)
        dx = self.x[1] - self.x[0]
        dy = self.y[1] - self.y[0]

        if Nx==1 and Ny>1:
            self.x = np.array( [0.0, ] )
            dx = dy

        if Ny==1 and Nx>1:
            self.y = np.array( [0.0, ] )
            dy = dx

        kx = 2 * np.pi * np.fft.fftfreq(Nx, dx)
        ky = 2 * np.pi * np.fft.fftfreq(Ny, dy)

        self.r = np.sqrt(self.x[:,None]**2 + self.y[None,:]**2 )
        self.Nr = self.r.size

        self.kr = np.sqrt(kx[:,None]**2 + ky[None,:]**2)
        self.kr2 = self.bcknd.to_device( self.kr**2 )

        self.kx = kx # [:,None] * np.ones_like(ky[None,:])
        self.ky = ky # [:,None] * np.ones_like(ky[None,:])

    def step(self, u, dz, overwrite=False, show_progress=False):
        """
        Propagate wave `u` over the distance `dz`.

        Parameters
        ----------
        u: 2darray of complex or double
            Spectral-radial distribution of the field to propagate.

        dz: float (m)
            Distance over which wave should be propagated.

        Returns
        -------
        u: 2darray of complex or double
            Overwritten array with the propagated field.
        """
        assert u.dtype == self.dtype

        if not overwrite:
            u_step = np.empty((self.Nkz, *self.shape_trns_new),
                              dtype=u.dtype)
        else:
            u_step = u

        if tqdm_available and show_progress:
            pbar = tqdm(total=self.Nkz, bar_format=bar_format)

        for ikz in range(self.Nkz):
            self.u_loc = self.bcknd.to_device(u[ikz,:])
            self.TST()

            phase_loc = self.kz[ikz]**2 - self.kr2
            phase_loc = self.bcknd.sqrt( (phase_loc>=0.)*phase_loc )
            self.u_ht *= self.bcknd.exp( 1j * dz * phase_loc )

            self.iTST()
            u_step[ikz] = self.bcknd.to_host(self.u_iht)
            if tqdm_available and show_progress:
                pbar.update(1)

        if tqdm_available and show_progress:
            pbar.close()

        return u_step

    def steps(self, u, dz=None, z_axis=None, show_progress=True):
        """
        Propagate wave `u` over the multiple steps.

        Parameters
        ----------
        u: 2darray of complex or double
            Spectral-radial distribution of the field to propagate.

        dz: array of floats (m)
            Steps over which wave should be propagated.

        z_axis: array of floats (m) (optional)
            Axis over which wave should be propagated. Overrides dz.

        Returns
        -------
        u: 3darray of complex or double
            Array with the steps of the propagated field.
        """
        assert u.dtype == self.dtype
        if z_axis is not None:
            dz = np.r_[z_axis[0], np.diff(z_axis)]

        Nsteps = len(dz)
        if Nsteps==0:
            return None

        u_steps = np.empty( (Nsteps, self.Nkz, *self.shape_trns_new),
                         dtype=u.dtype)

        if tqdm_available and show_progress:
            pbar = tqdm(total=self.Nkz*Nsteps, bar_format=bar_format)

        for ikz in range(self.Nkz):
            self.u_loc = self.bcknd.to_device(u[ikz])
            self.TST()
            ik_loc = self.bcknd.sqrt(self.bcknd.abs( self.kz[ikz]**2 - \
                                                     self.kr2 ))
            for i_step in range(Nsteps):
                self.u_ht *= self.bcknd.exp(1j * dz[i_step] * ik_loc )
                self.iTST()
                u_steps[i_step, ikz, :] = self.bcknd.to_host(self.u_iht)

                if tqdm_available and show_progress:
                    pbar.update(1)
                elif show_progress and not tqdm_available:
                    print(f"Done step {i_step} of {Nsteps} "+ \
                          f"for wavelength {ikz+1} of {self.Nkz}",
                          end='\r', flush=True)

        if tqdm_available and show_progress:
            pbar.close()

        return u_steps

    def initiate_stepping(self, u):
        """
        Initiate the stepped propagation mode. This mode allows computation
        of the consequent steps with access to the result on each step.
        In contrast to `step` can operate the `PropagatorResampling` class.

        Parameters
        ----------
        u: 2darray of complex or double
            Spectral-radial distribution of the field to be propagated.
        """
        assert u.dtype == self.dtype

        self.stepping_image = self.bcknd.to_device( np.zeros_like(u) )
        self.phase_loc = self.bcknd.to_device( np.zeros_like(u) )
        self.z_propagation = 0.0

        for ikz in range(self.Nkz):
            self.u_loc = self.bcknd.to_device(u[ikz,:])
            self.TST()

            self.stepping_image[ikz] = self.u_ht.copy()

            phase_loc = self.kz[ikz]**2 - self.kr2
            self.phase_loc[ikz] = self.bcknd.sqrt((phase_loc >= 0.)*phase_loc)

    def stepping(self, dz, u_out=None):
        """
        Perform a step in the stepped propagation mode. This mode allows computation
        of the consequent steps with access to the result on each step.
        In contrast to `step` can operate the `PropagatorResampling` class.

        Parameters
        ----------
        dz: float (m)
            Step over which wave should be propagated.

        u_out: 2darray of complex or double (optional)
            Array to which data should be written.
            If not provided will be allocated.
        """
        if u_out is None:
            u_out = np.empty((self.Nkz, *self.shape_trns_new),
                              dtype=self.dtype)

        for ikz in range(self.Nkz):
            self.stepping_image[ikz] *= self.bcknd.exp( \
                1j * dz * self.phase_loc[ikz] )
            self.u_ht = self.stepping_image[ikz].copy()
            self.iTST()
            u_out[ikz] = self.bcknd.to_host(self.u_iht)

        self.z_propagation += dz
        return u_out

    def get_Ez(self, ux):
        """
        Get a longitudinal field component from the transverse field using the
        Poisson equation in vacuum DIV.E = 0.
        Parameters
        ----------
        ux: 2darray of complex or double
            Spectral-radial distribution of the field to be propagated.
        """

        uz = np.zeros_like(ux)
        kx_2d = self.kx[:,None] * np.ones_like(self.ky[None,:])
        kx_2d = self.bcknd.to_device(kx_2d)

        for ikz in range(self.Nkz):
            self.u_loc = self.bcknd.to_device(ux[ikz,:])
            self.TST()

            kz_loc = self.bcknd.sqrt(self.bcknd.abs( self.kz[ikz]**2 - \
                                                           self.kr2 ))
            self.u_ht *= - kx_2d / kz_loc
            self.iTST()
            uz[ikz] = self.bcknd.to_host(self.u_iht)

        return uz


class PropagatorSymmetric(PropagatorCommon):
    """
    Class for the propagator with the Quasi-Discrete Hankel transform (QDHT)
    described in [M. Guizar-Sicairos, J.C. Gutiérrez-Vega, JOSAA 21, 53 (2004)].

    Contains methods to:
    - setup QDHT for TST;
    - perform a forward QDHT;
    - perform a inverse QDHT;

    This propagator uses same matrix for the forward and inverse transforms.
    The inverse transform can be truncated to a smaller radial size (same grid).
    """

    def __init__(self, r_axis, kz_axis, Nr_new=None,
                 mode=0, dtype=np.complex128,
                 backend=None, verbose=True):
        """
        Construct the propagator.

        Parameters
        ----------
        r_axis: tuple (Rmax, Nr)
          Here:
            Rmax: float (m)
                Radial size of the calculation domain.

            Nr: int
                Number of nodes of the radial grid.

        kz_axis: a tuple (k0, Lkz, Nkz) or a 1D numpy.array
            When tuple is given the axis is created using:

              k0: float (1/m)
                Central wavenumber of the spectral domain.

              Lkz: float (1/m)
                Total spectral width in units of wavenumbers.

              Nkz: int
                Number of spectral modes (wavenumbers) to resolve the temporal
                profile of the wave.

        Nr_new: int (optional)
            New number of nodes of the trancated radial grid. If not defined
            `Nr` will be used.

        mode: integer
            Order of Bessel function used for DHT

        dtype: type (optional)
            Data type to be used. Default is np.complex128.

        backend: string
            Backend to be used. See axiprop.backends.AVAILABLE_BACKENDS for the
            list of available options.
        """
        self.dtype = dtype
        self.mode = mode

        self.init_backend(backend, verbose)
        self.init_kz(kz_axis)
        self.r, self.Rmax, self.Nr = self.init_r_symmetric(r_axis)
        self.init_kr(self.Rmax, self.Nr)

        # Setup a truncated output grid if needed
        if Nr_new is None:
            self.Nr_new = self.Nr
            self.r_new = self.r
            self.Rmax_new = self.Rmax
        elif Nr_new>=self.Nr:
            self.Nr_new = sel.Nr
            self.r_new = self.r
            self.Rmax_new = self.Rmax
        else:
            self.Nr_new = Nr_new
            self.r_new = self.r[:Nr_new]
            self.Rmax_new = self.r_new.max() * self.alpha[Nr_new] \
                            / self.alpha[Nr_new-1]

        self.init_TST(Nr_new)

    def init_TST(self, Nr_new):
        """
        Setup QDHT transformation matrix and data buffers.

        Parameters
        ----------
        Nr_new: int
            New number of nodes of the trancated radial grid. If is `None`,
            `Nr` will be used.
        """
        Rmax = self.Rmax
        Nr = self.Nr
        Nr_new = self.Nr_new
        dtype = self.dtype
        mode = self.mode
        alpha = self.alpha
        alpha_np1 = self.alpha_np1

        self._j = self.bcknd.to_device( np.abs(jn(mode+1, alpha)) / Rmax )
        denominator = alpha_np1 * np.abs(jn(mode+1, alpha[:,None]) \
                                       * jn(mode+1, alpha[None,:]))

        self.TM = 2 * jn(mode, alpha[:,None] * alpha[None,:] / alpha_np1)\
                     / denominator
        self.TM = self.bcknd.to_device(self.TM, dtype)

        self.shape_trns = (Nr, )
        self.shape_trns_new = (Nr_new, )

        self.u_loc = self.bcknd.zeros(Nr, dtype)
        self.u_ht = self.bcknd.zeros(Nr, dtype)
        self.u_iht = self.bcknd.zeros(Nr_new, dtype)

        self.TST_matmul = self.bcknd.make_matmul(self.TM, self.u_loc, self.u_ht)
        self.iTST_matmul = self.bcknd.make_matmul(self.TM[:Nr_new],
                                           self.u_ht, self.u_iht)

    def TST(self):
        """
        Forward QDHT transform.
        """
        self.u_loc /= self._j
        self.u_ht = self.TST_matmul(self.TM, self.u_loc, self.u_ht)

    def iTST(self):
        """
        Inverse QDHT transform.
        """
        self.u_iht = self.iTST_matmul(self.TM[:self.Nr_new],
                                      self.u_ht, self.u_iht)
        self.u_iht *= self._j[:self.Nr_new]


class PropagatorResampling(PropagatorCommon):
    """
    Class for the propagator with the non-symmetric Discrete Hankel transform
    (DHT) and possible different sampling for the input and output radial grids.

    Contains methods to:
    - setup DHT/iDHT transforms for TST;
    - perform a forward DHT;
    - perform a inverse iDHT;

    This propagator creates DHT matrix using numeric inversion of the inverse iDHT.
    This method samples output field on an arbitrary uniform radial grid.
    """

    def __init__(self, r_axis, kz_axis, Rmax_new=None, Nr_new=None,
                 r_axis_new=None, mode=0, dtype=np.complex128,
                 backend=None, verbose=True):
        """
        Construct the propagator.

        Parameters
        ----------
        r_axis: multiple cases
            tuple (Rmax, Nr)
              Rmax: float (m)
                Radial size of the calculation domain.
              Nr: int
                Number of nodes of the radial grid.

            ndarray (m)
                Radial grid.

        kz_axis: a tuple (k0, Lkz, Nkz) or a 1D numpy.array
            When tuple is given the axis is created using:

              k0: float (1/m)
                Central wavenumber of the spectral domain.

              Lkz: float (1/m)
                Total spectral width in units of wavenumbers.

              Nkz: int
                Number of spectral modes (wavenumbers) to resolve the temporal
                profile of the wave.

        r_axis_new: multiple cases
            tuple (Rmax_new, Nr_new)
              Rmax_new: float (m)
                New radial size of the calculation domain.
              Nr_new: int
                New number of nodes of the radial grid.

            ndarray (m)
                New radial grid.

            None (default)
                No resampling

        Rmax_new: float (m) (optional)
            New radial size for the output calculation domain. If not defined
            `Rmax` will be used.

        Nr_new: int (optional)
            New number of nodes of the radial grid. If not defined `Nr`
            will be used.

        mode: integer
            Order of Bessel function used for DHT

        dtype: type (optional)
            Data type to be used. Default is np.complex128.

        backend: string
            Backend to be used. See axiprop.backends.AVAILABLE_BACKENDS for the
            list of available options.
        """
        self.dtype = dtype
        self.mode = mode

        self.init_backend(backend, verbose)
        self.init_kz(kz_axis)

        if type(r_axis) is tuple:
            self.r, self.Rmax, self.Nr = self.init_r_symmetric(r_axis)
        else:
            self.r, self.Rmax, self.Nr = self.init_r_sampled(r_axis)

        if Rmax_new is not None:
            r_axis_new = (Rmax_new, Nr_new)

        if r_axis_new is None:
            self.r_new, self.Rmax_new, self.Nr_new = self.r, self.Rmax, self.Nr
        elif type(r_axis_new) is tuple:
            self.r_new, self.Rmax_new, self.Nr_new = self.init_r_uniform(r_axis_new)
        else:
            self.r_new, self.Rmax_new, self.Nr_new = self.init_r_sampled(r_axis_new)

        self.init_kr(self.Rmax, self.Nr)
        self.init_TST()

    def init_TST(self):
        """
        Setup DHT transform and data buffers.

        Parameters
        ----------
        Rmax_new: float (m) (optional)
            New radial size for the output calculation domain. If not defined
            `Rmax` will be used.

        Nr_new: int
            New number of nodes of the radial grid. If is `None`, `Nr` will
            be used.
        """
        Nr = self.Nr
        Nr_new = self.Nr_new
        r = self.r
        r_new = self.r_new
        kr = self.kr
        dtype = self.dtype
        mode = self.mode

        self.TM = jn(mode, r[:,None] * kr[None,:])
        if mode == 0:
            self.TM = self.bcknd.inv_sqr_on_host(self.TM, dtype)
        else:
            self.TM = self.bcknd.inv_on_host(self.TM, dtype)

        self.TM = self.bcknd.to_device(self.TM)

        self.invTM = self.bcknd.to_device(\
            jn(mode, r_new[:,None] * kr[None,:]) , dtype)

        self.shape_trns = (Nr, )
        self.shape_trns_new = (Nr_new, )

        self.u_loc = self.bcknd.zeros(Nr, dtype)
        self.u_ht = self.bcknd.zeros(Nr, dtype)
        self.u_iht = self.bcknd.zeros(Nr_new, dtype)

        self.TST_matmul = self.bcknd.make_matmul(self.TM, self.u_loc, self.u_ht)
        self.iTST_matmul = self.bcknd.make_matmul(self.invTM, self.u_ht, self.u_iht)

    def TST(self):
        """
        Forward QDHT transform.
        """
        self.u_ht = self.TST_matmul(self.TM, self.u_loc, self.u_ht)

    def iTST(self):
        """
        Inverse QDHT transform.
        """
        self.u_iht = self.iTST_matmul(self.invTM, self.u_ht, self.u_iht)


class PropagatorFFT2(PropagatorCommon):
    """
    Class for the propagator with two-dimensional Fast Fourier transform (FFT2)
    for TST.

    Contains methods to:
    - setup TST data buffers;
    - perform a forward FFT;
    - perform a inverse FFT;
    """

    def __init__(self, x_axis, y_axis, kz_axis,
                 Rmax_new=None, Nr_new=None, dtype=np.complex128,
                 backend=None, verbose=True):
        """
        Construct the propagator.

        Parameters
        ----------
        x_axis: tuple (Lx, Nx)
          Define the x-axis grid with parameters:
            Lx: float (m)
                Full size of the calculation domain along x-axis.

            Nx: int
                Number of nodes of the x-grid. Better be an odd number,
                in order to make a symmteric grid.

        y_axis: tuple (Ly, Ny)
          Define the y-axis grid with parameters:
            Ly: float (m)
                Full size of the calculation domain along y-axis.

            Ny: int
                Number of nodes of the y-grid.Better be an odd number,
                in order to make a symmteric grid.

        kz_axis: a tuple (k0, Lkz, Nkz) or a 1D numpy.array
            When tuple is given the axis is created using:

              k0: float (1/m)
                Central wavenumber of the spectral domain.

              Lkz: float (1/m)
                Total spectral width in units of wavenumbers.

              Nkz: int
                Number of spectral modes (wavenumbers) to resolve the temporal
                profile of the wave.

        dtype: type (optional)
            Data type to be used. Default is np.complex128.

        backend: string
            Backend to be used. See axiprop.backends.AVAILABLE_BACKENDS for the
            list of available options.
        """
        self.dtype = dtype

        self.init_backend(backend, verbose)
        self.init_kz(kz_axis)
        self.init_xykxy_fft2(x_axis, y_axis)
        self.init_TST()

    def init_TST(self):
        """
        Setup data buffers for TST.
        """
        Nr = self.Nr
        Nx = self.Nx
        Ny = self.Ny
        self.Nr_new = Nr

        dtype = self.dtype

        self.shape_trns = (Nx, Ny)
        self.shape_trns_new = (Nx, Ny)

        self.u_loc = self.bcknd.zeros((Nx, Ny), dtype)
        self.u_ht = self.bcknd.zeros((Nx, Ny), dtype)
        self.u_iht = self.bcknd.zeros((Nx, Ny), dtype)

        self.fft2, self.ifft2 = self.bcknd.make_fft2(self.u_loc, self.u_ht, self.u_iht)

    def TST(self):
        """
        Forward FFT transform.
        """
        self.u_ht = self.fft2(self.u_loc, self.u_ht)

    def iTST(self):
        """
        Inverse FFT transform.
        """
        self.u_iht = self.ifft2(self.u_ht, self.u_iht)